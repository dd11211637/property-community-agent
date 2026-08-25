"""智能体持久化模型 — PRD §6.5.8。

两张表分工明确，**不可互相替代**：

* ``agent_conversations``（Conversation 业务表）保存会话**所有权**、当前房屋、
  接管状态与生命周期。它是业务事实，可被审计、可被人工坐席接管查询。
* ``agent_checkpoints``（Checkpointer）只保存**图执行状态**快照，用于中断恢复。
  它不是业务凭据：既不能替代 Conversation，也不能替代 AuditLog 或业务实体
  （工单 / 公告 / 账单 / 巡检任务）。

两者都以稳定的 ``conversation_id`` 作为 ``thread_id`` 关联。
"""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from property_agent.platform.infrastructure.orm_models import Base

# 会话生命周期
CONVERSATION_STATUSES = ("ACTIVE", "WAITING_CONFIRM", "HANDOVER", "CLOSED")


class ConversationModel(Base):
    """会话业务表：所有权 + 当前房屋 + 接管状态 + 生命周期。"""

    __tablename__ = "agent_conversations"
    __table_args__ = (
        Index("ix_agent_conversations_actor", "actor_id"),
        Index("ix_agent_conversations_community_status", "community_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="会话记录ID"
    )
    conversation_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="稳定会话标识，同时用作 LangGraph thread_id",
    )
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="会话所有者（可信身份，恢复时需重新校验）"
    )
    community_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="所属社区（数据隔离）"
    )
    current_house_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), comment="当前房屋；切换房屋时需清除相关槽位"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="ACTIVE",
        comment="生命周期: ACTIVE / WAITING_CONFIRM / HANDOVER / CLOSED",
    )
    handover_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否已转人工接管"
    )
    handover_ticket_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), comment="关联接管单ID"
    )
    last_intent: Mapped[str | None] = mapped_column(String(32), comment="最近一次识别到的意图")
    title: Mapped[str | None] = mapped_column(String(120), comment="会话标题，由首条用户消息生成")
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一条持久化消息时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="关闭时间")
    runtime_version: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="v1",
        comment="钉住的 runtime 版本（v1 legacy）；LangGraph 切换后用于分钟级回退",
    )


class AgentCheckpointModel(Base):
    """图执行状态检查点：应用重启后据此恢复待确认流程。"""

    __tablename__ = "agent_checkpoints"
    __table_args__ = (Index("ix_agent_checkpoints_pending", "pending_confirm"),)

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="检查点ID"
    )
    thread_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, comment="线程标识（= conversation_id）"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="快照版本号，每次保存 +1"
    )
    state: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        comment="GraphState 快照（JSON 安全形式）",
    )
    interrupt_node: Mapped[str | None] = mapped_column(
        String(64), comment="中断所在节点；非空表示流程暂停中"
    )
    pending_confirm: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否存在待确认的写操作"
    )
    runtime_cursor: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
        comment=(
            "v2 接受头指针：仅存 LangGraph 内部 checkpoint 的定位符"
            "（thread_id / checkpoint_ns / checkpoint_id）。v1 为 NULL。"
            "这是编排相关性数据，绝不作为业务/信任权威。"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )


class AgentMessageModel(Base):
    """Append-only conversation transcript for history and human handover."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        Index("ix_agent_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_agent_messages_actor_created", "actor_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agent_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    house_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32))
    message_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )


class AgentRunLeaseModel(Base):
    """运行 lease / fencing（P0 正确性底座）。

    一个 ``conversation_id`` 同一时刻只允许一个 live run（防止同会话并发
    lost-update）。``RunLeaseService`` 通过便携 ``INSERT … ON CONFLICT …``
    抢占 lease，``fence`` 单调递增；lease 过期后下一个 run 抢占时 fence
    +1，旧 worker 用过期 fence 写入会被 checkpoint CAS 拒绝。
    """

    __tablename__ = "agent_run_leases"

    thread_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="线程标识（= conversation_id）"
    )
    owner_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="当前持租的 run_id"
    )
    lease_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="lease 过期时间"
    )
    fence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="fencing token，每次抢占 +1"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="最后更新时间",
    )


class AgentActionApprovalModel(Base):
    """受控写操作的审批记录 — P0 原子性（deep-research-report.md §Approval 原子化）。

    生命周期 PENDING → APPROVED → CONSUMED（REJECTED / EXPIRED 为终态）。
    与业务 mutation / 审计 / Outbox **同事务**消费：``consume`` 必须在业务写
    的同一个 Session/UnitOfWork 内完成，要么全部提交，要么全部回滚，杜绝
    "已确认但未落库" 或 "已落库但未确认" 的中间态。
    """

    __tablename__ = "agent_action_approvals"
    __table_args__ = (
        Index("ix_agent_action_approvals_conversation", "conversation_id"),
        Index("ix_agent_action_approvals_actor", "actor_id"),
        # 同一会话 + 同一动作 + 同一参数指纹，至多一个开放（PENDING/APPROVED）审批，
        # 重复确认不会凭空产生第二个业务对象。
        Index(
            "ux_agent_approval_open_action",
            "conversation_id",
            "action",
            "params_hash",
            unique=True,
            sqlite_where=sa_text("status IN ('PENDING', 'APPROVED')"),
            postgresql_where=sa_text("status IN ('PENDING', 'APPROVED')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="审批记录ID"
    )
    conversation_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="稳定会话标识（= thread_id）"
    )
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="发起/确认操作的人"
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False, comment="动作类型")
    params_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="参数指纹（canonical_hash）"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", comment="审批状态"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="确认有效期起点"
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="审批通过时间"
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="业务消费时间"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="行版本，乐观锁"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )


class AgentMemoryModel(Base):
    """User-controlled long-term memory; never an authorization or business fact source."""

    __tablename__ = "agent_memories"
    __table_args__ = (
        Index("ix_agent_memories_owner_active", "actor_id", "community_id", "deleted_at"),
        Index("ix_agent_memories_house_active", "house_id", "deleted_at"),
        Index(
            "ux_agent_memory_automatic_candidate",
            "actor_id",
            "community_id",
            "source_evidence_id",
            "candidate_id",
            unique=True,
            sqlite_where=sa_text("source_evidence_id IS NOT NULL AND candidate_id IS NOT NULL"),
            postgresql_where=sa_text("source_evidence_id IS NOT NULL AND candidate_id IS NOT NULL"),
        ),
        Index(
            "ix_agent_memories_effective_scope",
            "actor_id",
            "community_id",
            "house_id",
            "lifecycle_status",
            "expires_at",
        ),
        Index(
            "ix_agent_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=sa_text(
                "embedding IS NOT NULL AND deleted_at IS NULL AND lifecycle_status = 'ACTIVE'"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    house_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    memory_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="SEMANTIC")
    content: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_key: Mapped[str | None] = mapped_column(String(128))
    source_conversation_id: Mapped[str | None] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="MEMORY_API")
    source_evidence_id: Mapped[str | None] = mapped_column(String(160))
    candidate_id: Mapped[str | None] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confirmation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="USER_CONFIRMED"
    )
    confidence: Mapped[float | None]
    confidence_method: Mapped[str | None] = mapped_column(String(64))
    lifecycle_status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    conflict_key: Mapped[str | None] = mapped_column(String(128))
    supersedes_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_memories.id", ondelete="SET NULL")
    )
    retention_class: Mapped[str] = mapped_column(String(24), nullable=False, default="LONG_LIVED")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_version: Mapped[str | None] = mapped_column(String(64))
    embedding_status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    cleanup_status: Mapped[str] = mapped_column(String(24), nullable=False, default="NOT_REQUIRED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
