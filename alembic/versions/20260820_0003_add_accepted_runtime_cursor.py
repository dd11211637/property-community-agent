"""add accepted LangGraph runtime cursor to agent_checkpoints

Revision ID: 20260820_0003
Revises: 20260820_0002
Create Date: 2026-08-20

PR4：应用接受头新增 ``runtime_cursor`` 列，仅存 LangGraph 内部 checkpoint 的精确定位符
（thread_id / checkpoint_ns / checkpoint_id），用于 v2 接受头发布协议（§18 / §18.1）。

本迁移完全 additive：仅新增一列，可安全回滚；不改动既有表结构/语义。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "20260820_0003"
down_revision: str | None = "20260820_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_checkpoints",
        sa.Column(
            "runtime_cursor",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
            comment=(
                "v2 接受头指针：仅存 LangGraph 内部 checkpoint 的定位符"
                "(thread_id/checkpoint_ns/checkpoint_id)；v1 为 NULL。相关性数据，非权威。"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_checkpoints", "runtime_cursor")
