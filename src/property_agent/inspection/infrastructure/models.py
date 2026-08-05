from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from property_agent.platform.infrastructure.orm_models import Base


# ----------------------------- 巡检任务 -----------------------------
class InspectionTaskModel(Base):
    __tablename__ = "inspection_tasks"
    __table_args__ = (
        UniqueConstraint("community_id", "business_no", name="uq_inspection_tasks_business_no"),
        UniqueConstraint(
            "created_by", "create_idempotency_key", name="uq_inspection_tasks_creator_idem"
        ),
        Index("ix_inspection_tasks_community_status", "community_id", "status"),
        Index("ix_inspection_tasks_assignee_status", "assignee_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    business_no: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    route_points: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    create_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PLANNED")
    assignee_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status_logs: Mapped[list["InspectionTaskStatusLogModel"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    records: Mapped[list["InspectionTaskRecordModel"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class InspectionTaskRecordModel(Base):
    __tablename__ = "inspection_task_records"
    __table_args__ = (Index("ix_inspection_task_records_task_created", "task_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("inspection_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    record_type: Mapped[str] = mapped_column(String(32), nullable=False)
    point: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str] = mapped_column(Text, nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attachment_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    is_supplement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    actual_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    task: Mapped[InspectionTaskModel] = relationship(back_populates="records")


class InspectionTaskStatusLogModel(Base):
    __tablename__ = "inspection_task_status_logs"
    __table_args__ = (
        Index("ix_inspection_task_status_logs_task_created", "task_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("inspection_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    action_code: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    task: Mapped[InspectionTaskModel] = relationship(back_populates="status_logs")


# ----------------------------- 安防事件 -----------------------------
class SecurityEventModel(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        UniqueConstraint("community_id", "business_no", name="uq_security_events_business_no"),
        UniqueConstraint(
            "reporter_id", "create_idempotency_key", name="uq_security_events_reporter_idem"
        ),
        Index("ix_security_events_community_status", "community_id", "status"),
        Index("ix_security_events_assignee_status", "assignee_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    business_no: Mapped[str] = mapped_column(String(40), nullable=False)
    source_task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reporter_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    create_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="REPORTED")
    assignee_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    grade_confirmed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status_logs: Mapped[list["SecurityEventStatusLogModel"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    disposals: Mapped[list["SecurityEventDisposalModel"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class SecurityEventDisposalModel(Base):
    __tablename__ = "security_event_disposals"
    __table_args__ = (Index("ix_security_event_disposals_event_created", "event_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("security_events.id", ondelete="RESTRICT"), nullable=False
    )
    handler_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    event: Mapped[SecurityEventModel] = relationship(back_populates="disposals")


class SecurityEventStatusLogModel(Base):
    __tablename__ = "security_event_status_logs"
    __table_args__ = (
        Index("ix_security_event_status_logs_event_created", "event_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("security_events.id", ondelete="RESTRICT"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    action_code: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    event: Mapped[SecurityEventModel] = relationship(back_populates="status_logs")
