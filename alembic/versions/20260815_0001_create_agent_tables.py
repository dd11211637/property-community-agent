"""create agent conversation & checkpoint tables for PRD 6.5.8

Revision ID: 20260815_0001
Revises: 20260808_0002
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260815_0001"
down_revision: str | None = "20260808_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


json_col = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # 会话业务表：所有权 / 当前房屋 / 接管状态 / 生命周期
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, comment="会话记录ID"),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            nullable=False,
            comment="稳定会话标识，同时用作 LangGraph thread_id",
        ),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=False, comment="会话所有者"),
        sa.Column("community_id", sa.Uuid(as_uuid=True), nullable=False, comment="所属社区"),
        sa.Column("current_house_id", sa.Uuid(as_uuid=True), nullable=True, comment="当前房屋"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="ACTIVE",
            comment="生命周期: ACTIVE / WAITING_CONFIRM / HANDOVER / CLOSED",
        ),
        sa.Column(
            "handover_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="是否已转人工接管",
        ),
        sa.Column(
            "handover_ticket_id", sa.Uuid(as_uuid=True), nullable=True, comment="关联接管单ID"
        ),
        sa.Column("last_intent", sa.String(length=32), nullable=True, comment="最近一次意图"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="更新时间",
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True, comment="关闭时间"),
        sa.UniqueConstraint("conversation_id", name="uq_agent_conversations_conversation_id"),
    )
    op.create_index("ix_agent_conversations_actor", "agent_conversations", ["actor_id"])
    op.create_index(
        "ix_agent_conversations_community_status",
        "agent_conversations",
        ["community_id", "status"],
    )

    # 图执行状态检查点：仅用于中断恢复，不替代业务表 / 审计日志
    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, comment="检查点ID"),
        sa.Column(
            "thread_id",
            sa.String(length=64),
            nullable=False,
            comment="线程标识（= conversation_id）",
        ),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default="1", comment="快照版本号"
        ),
        sa.Column("state", json_col, nullable=False, comment="GraphState 快照"),
        sa.Column("interrupt_node", sa.String(length=64), nullable=True, comment="中断所在节点"),
        sa.Column(
            "pending_confirm",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="是否存在待确认的写操作",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="更新时间",
        ),
        sa.UniqueConstraint("thread_id", name="uq_agent_checkpoints_thread_id"),
    )
    op.create_index("ix_agent_checkpoints_pending", "agent_checkpoints", ["pending_confirm"])


def downgrade() -> None:
    op.drop_index("ix_agent_checkpoints_pending", table_name="agent_checkpoints")
    op.drop_table("agent_checkpoints")
    op.drop_index("ix_agent_conversations_community_status", table_name="agent_conversations")
    op.drop_index("ix_agent_conversations_actor", table_name="agent_conversations")
    op.drop_table("agent_conversations")
