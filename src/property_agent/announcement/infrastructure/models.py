from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from property_agent.platform.database import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


class AnnouncementModel(Base):
    __tablename__ = "announcements"
    __table_args__ = (
        UniqueConstraint("community_id", "business_no", name="uq_announcements_business_no"),
        UniqueConstraint(
            "created_by", "create_idempotency_key", name="uq_announcements_creator_idem"
        ),
        Index("ix_announcements_community_status", "community_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    business_no: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    audience_condition: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    create_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manager_recheck_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    versions: Mapped[list["AnnouncementVersionModel"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["AnnouncementReviewModel"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
    )
    audience_snapshots: Mapped[list["AnnouncementAudienceSnapshotModel"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
    )
    withdrawals: Mapped[list["AnnouncementWithdrawalModel"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
    )


class AnnouncementVersionModel(Base):
    __tablename__ = "announcement_versions"
    __table_args__ = (
        UniqueConstraint("announcement_id", "version_no", name="uq_announcement_versions_no"),
        Index("ix_announcement_versions_announcement", "announcement_id", "version_no"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    announcement_id: Mapped[UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    audience_condition: Mapped[dict] = mapped_column(JsonType, nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    announcement: Mapped[AnnouncementModel] = relationship(back_populates="versions")


class AnnouncementReviewModel(Base):
    __tablename__ = "announcement_reviews"
    __table_args__ = (
        Index("ix_announcement_reviews_announcement", "announcement_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    announcement_id: Mapped[UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    announcement: Mapped[AnnouncementModel] = relationship(back_populates="reviews")


class AnnouncementAudienceSnapshotModel(Base):
    __tablename__ = "announcement_audience_snapshots"
    __table_args__ = (
        Index("ix_announcement_snapshots_announcement", "announcement_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    announcement_id: Mapped[UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="RESTRICT"), nullable=False
    )
    condition: Mapped[dict] = mapped_column(JsonType, nullable=False)
    member_ids: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False)
    samples: Mapped[list[dict]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    announcement: Mapped[AnnouncementModel] = relationship(back_populates="audience_snapshots")


class AnnouncementWithdrawalModel(Base):
    __tablename__ = "announcement_withdrawals"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    announcement_id: Mapped[UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="RESTRICT"), nullable=False
    )
    withdrawn_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    announcement: Mapped[AnnouncementModel] = relationship(back_populates="withdrawals")
