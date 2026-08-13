"""persist login lockout state

Revision ID: 20260819_0001
Revises: 20260818_0001
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260819_0001"
down_revision: str | None = "20260818_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username_normalized", sa.String(length=64), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "failure_count >= 0", name="ck_login_attempts_failure_count_non_negative"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "username_normalized",
            "source_ip",
            name="uq_login_attempts_username_source",
        ),
    )
    op.create_index(
        "ix_login_attempts_locked_until", "login_attempts", ["locked_until"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_login_attempts_locked_until", table_name="login_attempts")
    op.drop_table("login_attempts")
