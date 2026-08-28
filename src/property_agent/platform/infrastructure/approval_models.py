"""Platform-owned persistence model for authoritative Agent approvals."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, Uuid, func
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from property_agent.platform.infrastructure.orm_models import Base


class AgentActionApprovalModel(Base):
    """Approval consumed atomically with the authoritative business mutation."""

    __tablename__ = "agent_action_approvals"
    __table_args__ = (
        Index("ix_agent_action_approvals_conversation", "conversation_id"),
        Index("ix_agent_action_approvals_actor", "actor_id"),
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


__all__ = ["AgentActionApprovalModel"]
