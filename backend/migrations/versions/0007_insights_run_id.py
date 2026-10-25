"""insights.run_id — ссылка инсайта на запуск pipeline_runs, который его сделал (трассировка)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25

Уже сохранённым инсайтам проставляется последний успешный (done) запуск их компании: refresh
заменяет инсайты компании целиком, поэтому все текущие инсайты — из этого запуска.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "insights",
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_insights_run_id", "insights", ["run_id"])
    op.execute(
        """
        UPDATE insights i SET run_id = r.id
        FROM (
            SELECT DISTINCT ON (company_id) id, company_id
            FROM pipeline_runs WHERE status = 'done'
            ORDER BY company_id, started_at DESC, id DESC
        ) r
        WHERE r.company_id = i.company_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_insights_run_id", table_name="insights")
    op.drop_column("insights", "run_id")
