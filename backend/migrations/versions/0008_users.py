"""users — пользователи с паролем (bcrypt), ролью и компанией (вход, B8+B9)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column(
            "company_id",
            sa.Text(),
            sa.ForeignKey("companies.synthetic_u", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'ceo', 'cfo', 'treasurer', 'admin')", name="ck_users_role"
        ),
        sa.CheckConstraint("role = 'admin' OR company_id IS NOT NULL", name="ck_users_company"),
    )
    op.create_index("ix_users_company_id", "users", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_users_company_id", table_name="users")
    op.drop_table("users")
