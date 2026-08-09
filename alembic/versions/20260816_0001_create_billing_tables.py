"""create billing query and consultation tables

Revision ID: 20260816_0001
Revises: 20260815_0001
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260816_0001"
down_revision: str | None = "20260815_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_col = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "community_buildings",
        sa.Column("building_id", sa.String(32), primary_key=True),
        sa.Column("building_name", sa.String(64), nullable=False),
        sa.Column("building_type", sa.String(16), nullable=False, server_default="RESIDENTIAL"),
        sa.Column("total_floors", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("total_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("address", sa.String(256)),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "community_rooms",
        sa.Column("room_id", sa.String(32), primary_key=True),
        sa.Column(
            "building_id",
            sa.String(32),
            sa.ForeignKey("community_buildings.building_id"),
            nullable=False,
        ),
        sa.Column("room_number", sa.String(16), nullable=False),
        sa.Column("room_area", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("property_fee_rate", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("parking_spots", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parking_fee_rate", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OCCUPIED"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("building_id", "room_number", name="uq_room_building"),
    )
    op.create_table(
        "sys_users",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("user_name", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="owner"),
        sa.Column("building_id", sa.String(32), sa.ForeignKey("community_buildings.building_id")),
        sa.Column("room_id", sa.String(32), sa.ForeignKey("community_rooms.room_id")),
        sa.Column("phone", sa.String(20)),
        sa.Column("email", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "fee_bills",
        sa.Column("bill_id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("sys_users.user_id"), nullable=False),
        sa.Column(
            "room_id",
            sa.String(32),
            sa.ForeignKey("community_rooms.room_id"),
            nullable=False,
        ),
        sa.Column("bill_period", sa.String(7), nullable=False),
        sa.Column("property_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("utility_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("parking_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("late_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="UNPAID"),
        sa.Column("payment_time", sa.DateTime()),
        sa.Column("receipt_no", sa.String(32)),
        sa.Column("community_id", sa.String(64)),
        sa.Column("house_id", sa.String(64)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fee_type", sa.String(32)),
        sa.Column("source_time", sa.DateTime()),
        sa.Column("rule_version", sa.String(32)),
        sa.Column("rule_name", sa.String(128)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "room_id", "bill_period", name="uq_bill_user_room_period"),
    )
    for name, column in (
        ("idx_bills_user_id", "user_id"),
        ("idx_bills_status", "status"),
        ("idx_bills_due_date", "due_date"),
        ("idx_bills_period", "bill_period"),
        ("idx_bills_community", "community_id"),
        ("idx_bills_house", "house_id"),
        ("idx_bills_fee_type", "fee_type"),
    ):
        op.create_index(name, "fee_bills", [column])

    # Legacy payment and receipt records are read-only integration data. No public
    # endpoint in this application creates, refunds, or mutates them.
    op.create_table(
        "fee_payments",
        sa.Column("payment_id", sa.String(32), primary_key=True),
        sa.Column("bill_id", sa.String(32), sa.ForeignKey("fee_bills.bill_id"), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("sys_users.user_id"), nullable=False),
        sa.Column("pay_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("pay_method", sa.String(16), nullable=False),
        sa.Column("pay_status", sa.String(16), nullable=False),
        sa.Column("transaction_id", sa.String(64)),
        sa.Column("receipt_no", sa.String(32)),
        sa.Column("paid_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_payments_user_id", "fee_payments", ["user_id"])
    op.create_index("idx_payments_bill_id", "fee_payments", ["bill_id"])
    op.create_table(
        "fee_receipts",
        sa.Column("receipt_no", sa.String(32), primary_key=True),
        sa.Column("bill_id", sa.String(32), sa.ForeignKey("fee_bills.bill_id"), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("sys_users.user_id"), nullable=False),
        sa.Column(
            "payment_id",
            sa.String(32),
            sa.ForeignKey("fee_payments.payment_id"),
            nullable=False,
        ),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("property_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("utility_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("parking_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("late_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("issue_time", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "billing_rules",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("community_id", sa.String(64), nullable=False),
        sa.Column("fee_type", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("parameters", json_col),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_billing_rules_community", "billing_rules", ["community_id"])
    op.create_index("idx_billing_rules_fee_type", "billing_rules", ["fee_type"])
    op.create_table(
        "billing_consultations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("community_id", sa.String(64), nullable=False),
        sa.Column("house_id", sa.String(64)),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("bill_id", sa.String(32)),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("answer", sa.Text()),
        sa.Column("handler_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("idx_consultations_community", "billing_consultations", ["community_id"])
    op.create_index("idx_consultations_house", "billing_consultations", ["house_id"])
    op.create_index("idx_consultations_actor", "billing_consultations", ["actor_id"])
    op.create_index("idx_consultations_status", "billing_consultations", ["status"])


def downgrade() -> None:
    op.drop_index("idx_consultations_status", table_name="billing_consultations")
    op.drop_index("idx_consultations_actor", table_name="billing_consultations")
    op.drop_index("idx_consultations_house", table_name="billing_consultations")
    op.drop_index("idx_consultations_community", table_name="billing_consultations")
    op.drop_table("billing_consultations")
    op.drop_index("idx_billing_rules_fee_type", table_name="billing_rules")
    op.drop_index("idx_billing_rules_community", table_name="billing_rules")
    op.drop_table("billing_rules")
    op.drop_table("fee_receipts")
    op.drop_index("idx_payments_bill_id", table_name="fee_payments")
    op.drop_index("idx_payments_user_id", table_name="fee_payments")
    op.drop_table("fee_payments")
    for name in (
        "idx_bills_fee_type",
        "idx_bills_house",
        "idx_bills_community",
        "idx_bills_period",
        "idx_bills_due_date",
        "idx_bills_status",
        "idx_bills_user_id",
    ):
        op.drop_index(name, table_name="fee_bills")
    op.drop_table("fee_bills")
    op.drop_table("sys_users")
    op.drop_table("community_rooms")
    op.drop_table("community_buildings")
