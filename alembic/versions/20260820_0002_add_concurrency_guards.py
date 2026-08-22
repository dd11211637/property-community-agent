"""add P0 concurrency guards: run lease + action approvals + runtime version

Revision ID: 20260820_0002
Revises: 20260820_0001
Create Date: 2026-08-20

P0 正确性底座（deep-research-report.md §准备与并发控制修复）：

1. ``agent_run_leases``      —— 同一 conversation 单写者 lease/fencing，
                              防止两个长 LLM turn 并发跑出 checkpoint lost update。
2. ``agent_action_approvals`` —— 受控写操作的审批记录，PENDING→APPROVED→CONSUMED
                              与业务 mutation/审计/Outbox 同事务消费（原子性）。
3. ``agent_conversations.runtime_version`` —— v1 legacy runtime 钉版本号，
                              为后续 LangGraph 切换保留分钟级回退能力。

本迁移完全 additve：只新增表/列与索引，不改动既有表结构，可安全回滚。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_0002"
down_revision: str | None = "20260820_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. 运行 lease：同一 conversation 单写者 ─────────────────────
    # thread_id 即稳定的 conversation_id。owner_run_id 区分每次运行，
    # fence 单调递增，lease_until 过期后允许抢占。
    op.create_table(
        "agent_run_leases",
        sa.Column("thread_id", sa.String(length=64), primary_key=True, comment="= conversation_id"),
        sa.Column(
            "owner_run_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            comment="本次运行唯一标识，用于释放时校验归属",
        ),
        sa.Column(
            "lease_until",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="lease 过期时间；过期后允许其他运行抢占",
        ),
        sa.Column(
            "fence",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="fencing token：每次抢占 +1，旧 worker 凭旧 fence 被拒绝",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="最后抢占/续期时间",
        ),
    )

    # ── 2. 动作审批：受控写操作的原子审批记录 ───────────────────────
    op.create_table(
        "agent_action_approvals",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, comment="审批记录ID"),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            nullable=False,
            comment="稳定会话标识（= thread_id）",
        ),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=False, comment="发起/确认操作的人"),
        sa.Column("action", sa.String(length=128), nullable=False, comment="动作类型"),
        sa.Column(
            "params_hash",
            sa.String(length=64),
            nullable=False,
            comment="参数指纹（canonical_hash），确认回执必须带回同一枚",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="PENDING",
            comment="PENDING / APPROVED / REJECTED / CONSUMED / EXPIRED",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="确认有效期起点：恢复后必须重新校验（PRD §6.5.8）",
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True, comment="审批通过时间"),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True, comment="业务消费时间"),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="行版本，乐观锁",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="创建时间",
        ),
        sa.UniqueConstraint("id", name="uq_agent_action_approvals_id"),
    )
    op.create_index(
        "ix_agent_action_approvals_conversation",
        "agent_action_approvals",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_action_approvals_actor",
        "agent_action_approvals",
        ["actor_id"],
    )
    # 部分唯一索引：同一会话 + 同一动作 + 同一参数指纹，至多一个开放（PENDING/APPROVED）
    # 审批，杜绝重复确认产生第二个业务对象。
    op.create_index(
        "ux_agent_approval_open_action",
        "agent_action_approvals",
        ["conversation_id", "action", "params_hash"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING', 'APPROVED')"),
        postgresql_where=sa.text("status IN ('PENDING', 'APPROVED')"),
    )

    # ── 3. 会话表加运行时版本钉 ───────────────────────────────────
    op.add_column(
        "agent_conversations",
        sa.Column(
            "runtime_version",
            sa.String(length=16),
            nullable=False,
            server_default="v1",
            comment="钉住的 runtime 版本（v1 legacy）；LangGraph 切换后用于分钟级回退",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_conversations", "runtime_version")
    op.drop_index(
        "ux_agent_approval_open_action",
        table_name="agent_action_approvals",
        postgresql_where=sa.text("status IN ('PENDING', 'APPROVED')"),
    )
    op.drop_index("ix_agent_action_approvals_actor", table_name="agent_action_approvals")
    op.drop_index("ix_agent_action_approvals_conversation", table_name="agent_action_approvals")
    op.drop_table("agent_action_approvals")
    op.drop_table("agent_run_leases")
