"""separate message delivery and read state

Revision ID: 20260817_0001
Revises: 20260816_0001
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0001"
down_revision: str | None = "20260816_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "message_records",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Older builds encoded read state in the delivery status. Preserve the
    # evidence while restoring SENT as the actual delivery outcome.
    op.execute(
        "UPDATE message_records SET read_at = updated_at, status = 'SENT' WHERE status = 'READ'"
    )


def downgrade() -> None:
    op.execute("UPDATE message_records SET status = 'READ' WHERE read_at IS NOT NULL")
    op.drop_column("message_records", "read_at")
