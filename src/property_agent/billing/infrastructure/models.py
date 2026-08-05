from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from property_agent.platform.database import Base


class BillModel(Base):
    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("community_id", "external_bill_no", name="uq_bills_external_no"),
        CheckConstraint("amount >= 0", name="ck_bills_amount_nonnegative"),
        CheckConstraint("period_end >= period_start", name="ck_bills_period"),
        Index("ix_bills_community_house_period", "community_id", "house_id", "period_end"),
        Index("ix_bills_house_fee_period", "house_id", "fee_type", "period_end"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    external_bill_no: Mapped[str] = mapped_column(String(64), nullable=False)
    house_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    fee_type: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
