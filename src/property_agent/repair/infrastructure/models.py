from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
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

from property_agent.platform.database import Base


class WorkOrderModel(Base):
    __tablename__ = "work_orders"
    __table_args__ = (
        UniqueConstraint("community_id", "business_no", name="uq_work_orders_business_no"),
        UniqueConstraint(
            "reporter_id",
            "create_idempotency_key",
            name="uq_work_orders_reporter_idempotency",
        ),
        Index("ix_work_orders_community_status_created", "community_id", "status", "created_at"),
        Index("ix_work_orders_house_category_status", "house_id", "category", "status"),
        Index("ix_work_orders_assignee_status_updated", "assignee_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    business_no: Mapped[str] = mapped_column(String(40), nullable=False)
    house_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reporter_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    assignee_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    create_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status_logs: Mapped[list["WorkOrderStatusLogModel"]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan"
    )
    process_records: Mapped[list["WorkOrderProcessRecordModel"]] = relationship(
        back_populates="work_order", cascade="all, delete-orphan"
    )
    review: Mapped["WorkOrderReviewModel | None"] = relationship(
        back_populates="work_order", cascade="all, delete-orphan", uselist=False
    )


class WorkOrderStatusLogModel(Base):
    __tablename__ = "work_order_status_logs"
    __table_args__ = (
        Index("ix_work_order_status_logs_order_created", "work_order_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    work_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("work_orders.id", ondelete="RESTRICT"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    action_code: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operator_role: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    work_order: Mapped[WorkOrderModel] = relationship(back_populates="status_logs")


class WorkOrderProcessRecordModel(Base):
    __tablename__ = "work_order_process_records"
    __table_args__ = (
        Index("ix_work_order_process_records_order_created", "work_order_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    work_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("work_orders.id", ondelete="RESTRICT"), nullable=False
    )
    record_type: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    appointment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attachment_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    work_order: Mapped[WorkOrderModel] = relationship(back_populates="process_records")


class WorkOrderReviewModel(Base):
    __tablename__ = "work_order_reviews"
    __table_args__ = (
        UniqueConstraint("work_order_id", name="uq_work_order_reviews_order"),
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_work_order_reviews_rating"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    work_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("work_orders.id", ondelete="RESTRICT"), nullable=False
    )
    reviewer_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    work_order: Mapped[WorkOrderModel] = relationship(back_populates="review")
