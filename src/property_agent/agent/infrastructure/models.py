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

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
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
        String(64), nullable=False, unique=True,
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
        String(16), nullable=False, default="ACTIVE",
        comment="生命周期: ACTIVE / WAITING_CONFIRM / HANDOVER / CLOSED",
    )
    handover_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否已转人工接管"
    )
    handover_ticket_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), comment="关联接管单ID"
    )
    last_intent: Mapped[str | None] = mapped_column(
        String(32), comment="最近一次识别到的意图"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now(),
        comment="更新时间",
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="关闭时间"
    )


class AgentCheckpointModel(Base):
    """图执行状态检查点：应用重启后据此恢复待确认流程。"""

    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        Index("ix_agent_checkpoints_pending", "pending_confirm"),
    )

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
        JSON().with_variant(JSONB(), "postgresql"), nullable=False,
        comment="GraphState 快照（JSON 安全形式）",
    )
    interrupt_node: Mapped[str | None] = mapped_column(
        String(64), comment="中断所在节点；非空表示流程暂停中"
    )
    pending_confirm: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否存在待确认的写操作"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now(),
        comment="更新时间",
    )
