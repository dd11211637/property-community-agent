"""create platform foundation tables and attachment registry

The ten platform tables (PRD 5.1) previously existed only as SQLAlchemy models
created via ``init_db()``. This revision brings them into the migration chain
so ``alembic upgrade head`` produces a complete schema, and adds the
``attachments`` registry required by PRD 6.1 attachment validation.

Revision ID: 20260801_0001
Revises: 20260728_0001
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260801_0001"
down_revision: str | None = "20260728_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_OR_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # ── 1. communities ──────────────────────────────────────────
    op.create_table(
        "communities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_communities_name"),
    )
    op.create_index("ix_communities_status", "communities", ["status"])

    # ── 2. users ────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["community_id"], ["communities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("community_id", "username", name="uq_users_community_username"),
    )
    op.create_index("ix_users_community_status", "users", ["community_id", "status"])

    # ── 3. houses ───────────────────────────────────────────────
    op.create_table(
        "houses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("building", sa.String(length=32), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("room_no", sa.String(length=16), nullable=False),
        sa.Column("area", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["community_id"], ["communities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "community_id", "building", "unit", "room_no", name="uq_houses_address"
        ),
    )
    op.create_index("ix_houses_community_status", "houses", ["community_id", "status"])

    # ── 4. user_roles ───────────────────────────────────────────
    op.create_table(
        "user_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False, server_default="*"),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role", "scope", name="uq_user_roles"),
    )
    op.create_index("ix_user_roles_user", "user_roles", ["user_id"])

    # ── 5. user_house_bindings ──────────────────────────────────
    op.create_table(
        "user_house_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("house_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["house_id"], ["houses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "house_id", name="uq_user_house_bindings"),
    )
    op.create_index("ix_user_house_bindings_user", "user_house_bindings", ["user_id"])
    op.create_index("ix_user_house_bindings_house", "user_house_bindings", ["house_id"])

    # ── 6. confirmation_tokens ──────────────────────────────────
    op.create_table(
        "confirmation_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(length=256), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("parameter_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_confirmation_tokens_actor", "confirmation_tokens", ["actor_id"])
    op.create_index("ix_confirmation_tokens_expires", "confirmation_tokens", ["expires_at"])

    # ── 7. idempotency_records ──────────────────────────────────
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("response_snapshot", JSONB_OR_JSON, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_idempotency_actor_op_key"),
    )
    op.create_index("ix_idempotency_expires", "idempotency_records", ["created_at"])

    # ── 8. message_records ──────────────────────────────────────
    op.create_table(
        "message_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("receiver_id", sa.Uuid(), nullable=False),
        sa.Column("business_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_message_records_receiver_status", "message_records", ["receiver_id", "status"]
    )
    op.create_index(
        "ix_message_records_business", "message_records", ["business_type", "resource_id"]
    )

    # ── 9. audit_logs ───────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("parameter_summary", JSONB_OR_JSON, nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False, server_default="SUCCESS"),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_id", "created_at"])
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    # ── 10. handover_tickets ────────────────────────────────────
    op.create_table(
        "handover_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=True),
        sa.Column("requester_id", sa.Uuid(), nullable=True),
        sa.Column("resource_type", sa.String(length=32), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("payload", JSONB_OR_JSON, nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("queue", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_handover_tickets_status", "handover_tickets", ["status"])
    op.create_index("ix_handover_tickets_queue", "handover_tickets", ["queue"])
    op.create_index("ix_handover_tickets_community", "handover_tickets", ["community_id"])
    op.create_index(
        "ix_handover_tickets_resource", "handover_tickets", ["resource_type", "resource_id"]
    )

    # ── 11. attachments (PRD 6.1) ───────────────────────────────
    op.create_table(
        "attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("uploader_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="UPLOADING"),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("business_type", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_attachments_size_non_negative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachments_community_status", "attachments", ["community_id", "status"])
    op.create_index("ix_attachments_uploader", "attachments", ["uploader_id"])


def downgrade() -> None:
    op.drop_table("attachments")
    op.drop_table("handover_tickets")
    op.drop_table("audit_logs")
    op.drop_table("message_records")
    op.drop_table("idempotency_records")
    op.drop_table("confirmation_tokens")
    op.drop_table("user_house_bindings")
    op.drop_table("user_roles")
    op.drop_table("houses")
    op.drop_table("users")
    op.drop_table("communities")
