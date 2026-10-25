"""Вопросы по инсайтам (insight_questions), оценки (insight_feedback), задачи ask в jobs

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25

Внешнего ключа на insights нет: refresh удаляет и пересоздаёт инсайты компании, а вопросы и
оценки должны пережить это (метрики качества не обнуляются; id инсайта у агента устойчивый —
при повторном приходе той же новости история к нему возвращается).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = "status IN ('pending', 'running')"


def upgrade() -> None:
    # jobs: новый вид ask; «одна активная задача на компанию» — только у refresh
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", "kind IN ('refresh', 'ask')")
    op.drop_index("uq_jobs_active_company", table_name="jobs")
    op.create_index(
        "uq_jobs_active_company",
        "jobs",
        ["company_id", "kind"],
        unique=True,
        postgresql_where=sa.text(f"kind = 'refresh' AND {ACTIVE}"),
    )

    op.create_table(
        "insight_questions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("insight_id", sa.Text(), nullable=False),
        sa.Column(
            "company_id",
            sa.Text(),
            sa.ForeignKey("companies.synthetic_u", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "sources", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column(
            "job_id", sa.Text(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')", name="ck_insight_questions_status"
        ),
        sa.CheckConstraint("char_length(question) <= 500", name="ck_insight_questions_len"),
    )
    op.create_index(
        "ix_insight_questions_insight", "insight_questions", ["insight_id", "created_at"]
    )

    op.create_table(
        "insight_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("insight_id", sa.Text(), nullable=False),
        sa.Column(
            "company_id",
            sa.Text(),
            sa.ForeignKey("companies.synthetic_u", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("insight_type", sa.String(12), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("rating", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(12), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("insight_id", "user_id", name="uq_insight_feedback_user"),
        sa.CheckConstraint("rating IN ('useful', 'not_useful')", name="ck_insight_feedback_rating"),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ('inaccurate', 'irrelevant', 'unclear', 'other')",
            name="ck_insight_feedback_reason",
        ),
        sa.CheckConstraint(
            "comment IS NULL OR char_length(comment) <= 500", name="ck_insight_feedback_comment"
        ),
    )


def downgrade() -> None:
    op.drop_table("insight_feedback")
    op.drop_index("ix_insight_questions_insight", table_name="insight_questions")
    op.drop_table("insight_questions")
    op.execute("DELETE FROM jobs WHERE kind = 'ask'")
    op.drop_index("uq_jobs_active_company", table_name="jobs")
    op.create_index(
        "uq_jobs_active_company",
        "jobs",
        ["company_id", "kind"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", "kind IN ('refresh')")
