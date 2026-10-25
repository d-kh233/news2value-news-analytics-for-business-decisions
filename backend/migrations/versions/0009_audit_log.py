"""audit_log — журнал вызовов ML-функций и входов (B8+B9)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("tool", sa.Text(), nullable=True),
        sa.Column("company_id", sa.Text(), nullable=True),
        sa.Column(
            "input_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_company_ts", "audit_log", ["company_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_company_ts", table_name="audit_log")
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
    op.drop_table("audit_log")
