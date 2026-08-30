"""Add resident service details and request attachment links to work orders.

Revision ID: 20260830_0001
Revises: 20260828_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_0001"
down_revision: str | None = "20260828_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_orders", sa.Column("contact_name", sa.String(128), nullable=True))
    op.add_column("work_orders", sa.Column("contact_phone", sa.String(32), nullable=True))
    op.add_column("work_orders", sa.Column("access_instructions", sa.Text(), nullable=True))
    op.add_column(
        "work_orders",
        sa.Column(
            "preferred_time_windows",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "work_orders",
        sa.Column(
            "request_attachment_ids",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("work_orders", "request_attachment_ids")
    op.drop_column("work_orders", "preferred_time_windows")
    op.drop_column("work_orders", "access_instructions")
    op.drop_column("work_orders", "contact_phone")
    op.drop_column("work_orders", "contact_name")
