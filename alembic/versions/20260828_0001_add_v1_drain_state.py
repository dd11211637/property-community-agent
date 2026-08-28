"""add governed v1 drain state

Revision ID: 20260828_0001
Revises: 20260825_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260828_0001"
down_revision: str | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_conversations", sa.Column("v1_drain_state", sa.String(24)))
    op.add_column("agent_conversations", sa.Column("v1_drain_policy_version", sa.String(64)))
    op.add_column("agent_conversations", sa.Column("v1_drain_idempotency_key", sa.String(128)))
    op.add_column("agent_conversations", sa.Column("v1_drained_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint(
        "uq_agent_conversations_v1_drain_idempotency",
        "agent_conversations",
        ["v1_drain_idempotency_key"],
    )
    op.create_index(
        "ix_agent_conversations_runtime_drain_status",
        "agent_conversations",
        ["runtime_version", "v1_drain_state", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_conversations_runtime_drain_status", table_name="agent_conversations")
    op.drop_constraint(
        "uq_agent_conversations_v1_drain_idempotency",
        "agent_conversations",
        type_="unique",
    )
    op.drop_column("agent_conversations", "v1_drained_at")
    op.drop_column("agent_conversations", "v1_drain_idempotency_key")
    op.drop_column("agent_conversations", "v1_drain_policy_version")
    op.drop_column("agent_conversations", "v1_drain_state")
