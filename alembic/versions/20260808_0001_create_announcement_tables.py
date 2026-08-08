"""create announcement tables

Revision ID: 20260808_0001
Revises: 20260731_0001
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260808_0001"
down_revision: str | None = "20260731_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "announcements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("business_no", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("audience_condition", jsonb, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("create_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "manager_recheck_required", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("community_id", "business_no", name="uq_announcements_business_no"),
        sa.UniqueConstraint(
            "created_by", "create_idempotency_key", name="uq_announcements_creator_idem"
        ),
    )
    op.create_index(
        "ix_announcements_community_status", "announcements", ["community_id", "status"]
    )
    op.create_table(
        "announcement_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("audience_condition", jsonb, nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("announcement_id", "version_no", name="uq_announcement_versions_no"),
    )
    op.create_index(
        "ix_announcement_versions_announcement",
        "announcement_versions",
        ["announcement_id", "version_no"],
    )
    op.create_table(
        "announcement_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_announcement_reviews_announcement",
        "announcement_reviews",
        ["announcement_id", "created_at"],
    )
    op.create_table(
        "announcement_audience_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        sa.Column("condition", jsonb, nullable=False),
        sa.Column("member_ids", jsonb, nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("samples", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_announcement_snapshots_announcement",
        "announcement_audience_snapshots",
        ["announcement_id", "created_at"],
    )
    op.create_table(
        "announcement_withdrawals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        sa.Column("withdrawn_by", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("announcement_withdrawals")
    op.drop_index(
        "ix_announcement_snapshots_announcement", table_name="announcement_audience_snapshots"
    )
    op.drop_table("announcement_audience_snapshots")
    op.drop_index("ix_announcement_reviews_announcement", table_name="announcement_reviews")
    op.drop_table("announcement_reviews")
    op.drop_index("ix_announcement_versions_announcement", table_name="announcement_versions")
    op.drop_table("announcement_versions")
    op.drop_index("ix_announcements_community_status", table_name="announcements")
    op.drop_table("announcements")
