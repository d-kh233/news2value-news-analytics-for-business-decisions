"""Профили компаний (company_profiles) и колонки companies.source, companies.revenue_known

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Колонки добавляются к существующей таблице: уже загруженные строки остаются
    op.add_column(
        "companies",
        sa.Column("source", sa.String(10), nullable=False, server_default="bank"),
    )
    op.create_check_constraint("ck_companies_source", "companies", "source IN ('bank', 'manual')")
    op.add_column(
        "companies",
        sa.Column("revenue_known", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Уже загруженные строки: выручка известна, только если в CSV было число
    op.execute("UPDATE companies SET revenue_known = (revenue_company IS NOT NULL)")

    op.create_table(
        "company_profiles",
        sa.Column(
            "company_id",
            sa.Text(),
            sa.ForeignKey("companies.synthetic_u", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "auto_data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "manual_data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("company_profiles")
    op.drop_column("companies", "revenue_known")
    op.drop_constraint("ck_companies_source", "companies", type_="check")
    op.drop_column("companies", "source")
