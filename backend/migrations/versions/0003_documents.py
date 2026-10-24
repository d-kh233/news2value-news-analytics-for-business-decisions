"""Документы из источников (documents): новости RSS и PDF-аналитика банка

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("status", sa.String(12), nullable=False),
        sa.CheckConstraint("kind IN ('external_news', 'bank_analytics')", name="ck_documents_kind"),
        sa.CheckConstraint("status IN ('ok', 'empty_text', 'error')", name="ck_documents_status"),
        sa.UniqueConstraint("content_hash", name="uq_documents_content_hash"),
    )
    # url уникален только среди строк, где он есть
    op.create_index(
        "uq_documents_url",
        "documents",
        ["url"],
        unique=True,
        postgresql_where=sa.text("url IS NOT NULL"),
    )
    op.create_index("ix_documents_kind_published_at", "documents", ["kind", "published_at"])


def downgrade() -> None:
    op.drop_index("ix_documents_kind_published_at", table_name="documents")
    op.drop_index("uq_documents_url", table_name="documents")
    op.drop_table("documents")
