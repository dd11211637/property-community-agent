"""create repair work-order tables

Revision ID: 20260723_0001
Revises:
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260723_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("business_no", sa.String(length=40), nullable=False),
        sa.Column("house_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("location", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("create_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "community_id", "business_no", name="uq_work_orders_business_no"
        ),
        sa.UniqueConstraint(
            "reporter_id",
            "create_idempotency_key",
            name="uq_work_orders_reporter_idempotency",
        ),
    )
    op.create_index(
        "ix_work_orders_community_status_created",
        "work_orders",
        ["community_id", "status", "created_at"],
    )
    op.create_index(
        "ix_work_orders_house_category_status",
        "work_orders",
        ["house_id", "category", "status"],
    )
    op.create_index(
        "ix_work_orders_assignee_status_updated",
        "work_orders",
        ["assignee_id", "status", "updated_at"],
    )

    op.create_table(
        "work_order_status_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("operator_role", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_work_order_status_logs_order_created",
        "work_order_status_logs",
        ["work_order_id", "created_at"],
    )

    op.create_table(
        "work_order_process_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attachment_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_work_order_process_records_order_created",
        "work_order_process_records",
        ["work_order_id", "created_at"],
    )

    op.create_table(
        "work_order_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rating >= 1 AND rating <= 5", name="ck_work_order_reviews_rating"
        ),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_order_id", name="uq_work_order_reviews_order"),
    )


def downgrade() -> None:
    op.drop_table("work_order_reviews")
    op.drop_index(
        "ix_work_order_process_records_order_created",
        table_name="work_order_process_records",
    )
    op.drop_table("work_order_process_records")
    op.drop_index(
        "ix_work_order_status_logs_order_created", table_name="work_order_status_logs"
    )
    op.drop_table("work_order_status_logs")
    op.drop_index("ix_work_orders_assignee_status_updated", table_name="work_orders")
    op.drop_index("ix_work_orders_house_category_status", table_name="work_orders")
    op.drop_index("ix_work_orders_community_status_created", table_name="work_orders")
    op.drop_table("work_orders")
