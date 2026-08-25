"""govern long-term memory with pgvector and provenance

Revision ID: 20260825_0001
Revises: 20260820_0003
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "20260825_0001"
down_revision: str | None = "20260820_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    columns = (
        sa.Column("memory_kind", sa.String(24), nullable=False, server_default="SEMANTIC"),
        sa.Column("canonical_key", sa.String(128)),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="MEMORY_API"),
        sa.Column("source_evidence_id", sa.String(160)),
        sa.Column("candidate_id", sa.String(64)),
        sa.Column(
            "provenance",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "confirmation_status", sa.String(24), nullable=False, server_default="USER_CONFIRMED"
        ),
        sa.Column("confidence", sa.Float()),
        sa.Column("confidence_method", sa.String(64)),
        sa.Column("lifecycle_status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("conflict_key", sa.String(128)),
        sa.Column(
            "supersedes_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("agent_memories.id", ondelete="SET NULL"),
        ),
        sa.Column("retention_class", sa.String(24), nullable=False, server_default="LONG_LIVED"),
        sa.Column("embedding", Vector(1536)),
        sa.Column("embedding_model", sa.String(128)),
        sa.Column("embedding_version", sa.String(64)),
        sa.Column("embedding_status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("cleanup_status", sa.String(24), nullable=False, server_default="NOT_REQUIRED"),
    )
    for column in columns:
        op.add_column("agent_memories", column)
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY actor_id, community_id, house_id
                       ORDER BY updated_at DESC, id DESC
                   ) AS position
            FROM agent_memories
            WHERE memory_type = 'COMMUNICATION' AND deleted_at IS NULL
        )
        UPDATE agent_memories AS memory
        SET lifecycle_status = 'SUPERSEDED'
        FROM ranked
        WHERE memory.id = ranked.id AND ranked.position > 1
        """
    )
    op.execute(
        """
        UPDATE agent_memories
        SET canonical_key = 'communication-preference',
            conflict_key = 'communication-preference'
        WHERE memory_type = 'COMMUNICATION'
        """
    )
    op.create_index(
        "ix_agent_memories_effective_scope",
        "agent_memories",
        ["actor_id", "community_id", "house_id", "lifecycle_status", "expires_at"],
    )
    op.create_index(
        "ux_agent_memory_automatic_candidate",
        "agent_memories",
        ["actor_id", "community_id", "source_evidence_id", "candidate_id"],
        unique=True,
        postgresql_where=sa.text("source_evidence_id IS NOT NULL AND candidate_id IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_memories_embedding_hnsw",
        "agent_memories",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text(
            "embedding IS NOT NULL AND deleted_at IS NULL AND lifecycle_status = 'ACTIVE'"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_memories_embedding_hnsw", table_name="agent_memories")
    op.drop_index("ux_agent_memory_automatic_candidate", table_name="agent_memories")
    op.drop_index("ix_agent_memories_effective_scope", table_name="agent_memories")
    for name in (
        "cleanup_status",
        "embedding_status",
        "embedding_version",
        "embedding_model",
        "embedding",
        "retention_class",
        "supersedes_id",
        "conflict_key",
        "lifecycle_status",
        "confidence_method",
        "confidence",
        "confirmation_status",
        "provenance",
        "candidate_id",
        "source_evidence_id",
        "source_type",
        "canonical_key",
        "memory_kind",
    ):
        op.drop_column("agent_memories", name)
