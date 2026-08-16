"""create agent transcript and user-controlled memory tables

Revision ID: 20260820_0001
Revises: 20260819_0001
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260820_0001"
down_revision: str | None = "20260819_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_col = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("agent_conversations", sa.Column("title", sa.String(120), nullable=True))
    op.add_column(
        "agent_conversations",
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(64),
            sa.ForeignKey("agent_conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("community_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("house_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("metadata", json_col, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_agent_messages_conversation_created",
        "agent_messages",
        ["conversation_id", "created_at"],
    )
    op.create_index("ix_agent_messages_actor_created", "agent_messages", ["actor_id", "created_at"])
    op.create_table(
        "agent_memories",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("community_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("house_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("memory_type", sa.String(32), nullable=False),
        sa.Column("content", sa.String(500), nullable=False),
        sa.Column("source_conversation_id", sa.String(64), nullable=True),
        sa.Column("confirmed_by_user", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_memories_owner_active",
        "agent_memories",
        ["actor_id", "community_id", "deleted_at"],
    )
    op.create_index("ix_agent_memories_house_active", "agent_memories", ["house_id", "deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_memories_house_active", table_name="agent_memories")
    op.drop_index("ix_agent_memories_owner_active", table_name="agent_memories")
    op.drop_table("agent_memories")
    op.drop_index("ix_agent_messages_actor_created", table_name="agent_messages")
    op.drop_index("ix_agent_messages_conversation_created", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_column("agent_conversations", "last_message_at")
    op.drop_column("agent_conversations", "title")
