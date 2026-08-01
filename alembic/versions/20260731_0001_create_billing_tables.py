"""create read-only billing table

Revision ID: 20260731_0001
Revises: 20260728_0001
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260731_0001"
down_revision: str | None = "20260728_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("external_bill_no", sa.String(length=64), nullable=False),
        sa.Column("house_id", sa.Uuid(), nullable=False),
        sa.Column("fee_type", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "detail_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("payment_status", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_bills_amount_nonnegative"),
        sa.CheckConstraint("period_end >= period_start", name="ck_bills_period"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "community_id",
            "external_bill_no",
            name="uq_bills_external_no",
        ),
    )
    op.create_index(
        "ix_bills_community_house_period",
        "bills",
        ["community_id", "house_id", "period_end"],
    )
    op.create_index(
        "ix_bills_house_fee_period",
        "bills",
        ["house_id", "fee_type", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_bills_house_fee_period", table_name="bills")
    op.drop_index("ix_bills_community_house_period", table_name="bills")
    op.drop_table("bills")
