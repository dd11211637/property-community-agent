"""enforce bill total invariant

Revision ID: 20260818_0001
Revises: 20260817_0001
Create Date: 2026-08-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260818_0001"
down_revision: str | None = "20260817_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_fee_bills_total_matches_components",
        "fee_bills",
        "total_amount = property_fee + utility_fee + parking_fee + late_fee",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_fee_bills_total_matches_components",
        "fee_bills",
        type_="check",
    )
