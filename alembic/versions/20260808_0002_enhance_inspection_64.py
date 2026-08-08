"""enhance inspection & security-event for PRD 6.4

Revision ID: 20260808_0002
Revises: 20260808_0001
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260808_0002"
down_revision: str | None = "20260808_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


json_col = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # 巡检任务：AI 异常建议（数据结构 + 待人工确认标识）
    op.add_column("inspection_tasks", sa.Column("ai_suggestions", json_col, nullable=True))
    op.add_column(
        "inspection_tasks",
        sa.Column(
            "ai_pending_confirm",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # 巡检记录：补交原因（PRD 6.4）
    op.add_column(
        "inspection_task_records",
        sa.Column("supplement_reason", sa.Text(), nullable=True),
    )

    # 安防事件：上报来源 MANUAL/AI（PRD 6.4：模型失败时允许人工直接上报）
    op.add_column(
        "security_events",
        sa.Column(
            "report_source",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'MANUAL'"),
        ),
    )

    op.execute(
        "UPDATE inspection_tasks SET ai_pending_confirm = false WHERE ai_pending_confirm IS NULL"
    )
    op.execute(
        "UPDATE security_events SET report_source = 'MANUAL' WHERE report_source IS NULL"
    )


def downgrade() -> None:
    op.drop_column("security_events", "report_source")
    op.drop_column("inspection_task_records", "supplement_reason")
    op.drop_column("inspection_tasks", "ai_pending_confirm")
    op.drop_column("inspection_tasks", "ai_suggestions")
