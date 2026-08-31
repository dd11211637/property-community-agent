"""enforce the V2-only agent runtime

Revision ID: 20260830_0001
Revises: 20260828_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0001"
down_revision: str | None = "20260828_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    retired_count = connection.execute(
        sa.text("SELECT count(*) FROM agent_conversations WHERE runtime_version <> 'v2'")
    ).scalar_one()
    if retired_count:
        raise RuntimeError(
            "V2-only migration blocked: archive and remove retired runtime conversations first"
        )

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
    op.alter_column("agent_conversations", "runtime_version", server_default="v2")
    op.create_check_constraint(
        "ck_agent_conversations_runtime_v2_only",
        "agent_conversations",
        "runtime_version = 'v2'",
    )
    op.create_index(
        "ix_agent_conversations_runtime_status",
        "agent_conversations",
        ["runtime_version", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_conversations_runtime_status", table_name="agent_conversations")
    op.drop_constraint(
        "ck_agent_conversations_runtime_v2_only",
        "agent_conversations",
        type_="check",
    )
    op.alter_column("agent_conversations", "runtime_version", server_default="v1")
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
