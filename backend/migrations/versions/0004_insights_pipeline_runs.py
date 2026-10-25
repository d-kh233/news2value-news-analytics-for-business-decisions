"""Инсайты компаний (insights) и журнал запусков refresh (pipeline_runs)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insights",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Text(),
            sa.ForeignKey("companies.synthetic_u", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default=sa.text("'{}'::bigint[]"),
        ),
        # вся карточка в формате API, включая details (6 блоков llm.schemas.Insight)
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("type", sa.String(12), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("generated_by", sa.String(10), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("type IN ('risk', 'opportunity', 'event')", name="ck_insights_type"),
        sa.CheckConstraint(
            "generated_by IN ('llm', 'rules', 'fake')", name="ck_insights_generated_by"
        ),
        sa.CheckConstraint("importance BETWEEN 0 AND 100", name="ck_insights_importance"),
    )
    op.create_index("ix_insights_company_id", "insights", ["company_id"])

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "company_id",
            sa.Text(),
            sa.ForeignKey("companies.synthetic_u", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(10), nullable=False, server_default="running"),
        sa.Column(
            "steps", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'empty', 'failed')", name="ck_pipeline_runs_status"
        ),
    )
    op.create_index(
        "ix_pipeline_runs_company_started", "pipeline_runs", ["company_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_company_started", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index("ix_insights_company_id", table_name="insights")
    op.drop_table("insights")
