"""create inspection and security-event tables

Revision ID: 20260728_0001
Revises: 20260723_0001
Create Date: 2026-07-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260728_0001"
down_revision: str | None = "20260723_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inspection_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("business_no", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("route_points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("create_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("community_id", "business_no", name="uq_inspection_tasks_business_no"),
        sa.UniqueConstraint("created_by", "create_idempotency_key", name="uq_inspection_tasks_creator_idem"),
    )
    op.create_index("ix_inspection_tasks_community_status", "inspection_tasks", ["community_id", "status"])
    op.create_index("ix_inspection_tasks_assignee_status", "inspection_tasks", ["assignee_id", "status"])

    op.create_table(
        "inspection_task_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column("point", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_supplement", sa.Boolean(), nullable=False),
        sa.Column("actual_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["inspection_tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inspection_task_records_task_created", "inspection_task_records", ["task_id", "created_at"])

    op.create_table(
        "inspection_task_status_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("operator_role", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["inspection_tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inspection_task_status_logs_task_created",
        "inspection_task_status_logs",
        ["task_id", "created_at"],
    )

    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("business_no", sa.String(length=40), nullable=False),
        sa.Column("source_task_id", sa.Uuid(), nullable=True),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("location", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("create_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("grade_confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("community_id", "business_no", name="uq_security_events_business_no"),
        sa.UniqueConstraint("reporter_id", "create_idempotency_key", name="uq_security_events_reporter_idem"),
    )
    op.create_index("ix_security_events_community_status", "security_events", ["community_id", "status"])
    op.create_index("ix_security_events_assignee_status", "security_events", ["assignee_id", "status"])

    op.create_table(
        "security_event_disposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("handler_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("attachment_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["security_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_security_event_disposals_event_created",
        "security_event_disposals",
        ["event_id", "created_at"],
    )

    op.create_table(
        "security_event_status_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("operator_role", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["security_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_security_event_status_logs_event_created",
        "security_event_status_logs",
        ["event_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_security_event_status_logs_event_created", table_name="security_event_status_logs")
    op.drop_table("security_event_status_logs")
    op.drop_index("ix_security_event_disposals_event_created", table_name="security_event_disposals")
    op.drop_table("security_event_disposals")
    op.drop_index("ix_security_events_assignee_status", table_name="security_events")
    op.drop_index("ix_security_events_community_status", table_name="security_events")
    op.drop_table("security_events")
    op.drop_index("ix_inspection_task_status_logs_task_created", table_name="inspection_task_status_logs")
    op.drop_table("inspection_task_status_logs")
    op.drop_index("ix_inspection_task_records_task_created", table_name="inspection_task_records")
    op.drop_table("inspection_task_records")
    op.drop_index("ix_inspection_tasks_assignee_status", table_name="inspection_tasks")
    op.drop_index("ix_inspection_tasks_community_status", table_name="inspection_tasks")
    op.drop_table("inspection_tasks")
