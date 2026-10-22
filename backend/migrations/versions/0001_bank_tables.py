"""Таблицы данных банка: companies, persons, person_company_links, payments, rejected_rows

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("synthetic_u", sa.Text(), primary_key=True),
        sa.Column("org_type", sa.Text()),
        sa.Column("okfs_name", sa.Text()),
        sa.Column("company_age_years_gradation", sa.Text()),
        sa.Column("eco_mark_label", sa.Text()),
        sa.Column("mainokved_code", sa.Text()),
        sa.Column("mainokved_name", sa.Text()),
        sa.Column("negativemark", sa.Boolean()),
        sa.Column("amount_workers_fns_gradation", sa.Text()),
        sa.Column("amount_workers_spark_gradation", sa.Text()),
        sa.Column("cl_type", sa.Text()),
        sa.Column("cl_product_rko_mark", sa.Boolean()),
        sa.Column("cl_product_gar_mark", sa.Boolean()),
        sa.Column("cl_product_loa_mark", sa.Boolean()),
        sa.Column("cl_product_lea_mark", sa.Boolean()),
        sa.Column("cl_product_dep_mark", sa.Boolean()),
        sa.Column("cl_product_zpp_mark", sa.Boolean()),
        sa.Column("fts_flag", sa.Boolean()),
        sa.Column("fts_country_name", sa.Text()),
        sa.Column("factoring_mark", sa.Boolean()),
        sa.Column("revenue_company", sa.BigInteger()),
        sa.Column("revenue_company_raw", sa.Text()),
        sa.Column("group_id", sa.Text()),
        sa.Column("city", sa.Text()),
        sa.Column("region", sa.Text()),
    )
    op.create_index("ix_companies_group_id", "companies", ["group_id"])

    op.create_table(
        "persons",
        sa.Column("synthetic_f", sa.Text(), primary_key=True),
    )

    op.create_table(
        "person_company_links",
        sa.Column("synthetic_f", sa.Text(), primary_key=True),
        sa.Column("synthetic_u", sa.Text(), primary_key=True),
        sa.Column("city_name", sa.Text()),
        sa.Column("rc_name", sa.Text()),
        sa.Column("is_known_company", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_person_company_links_synthetic_u", "person_company_links", ["synthetic_u"])

    op.create_table(
        "payments",
        sa.Column("payer", sa.Text(), primary_key=True),
        sa.Column("beneficiary", sa.Text(), primary_key=True),
        sa.Column("pay_amt", sa.BigInteger()),
        sa.Column("pay_amt_raw", sa.Text()),
        sa.Column("pay_cnt", sa.BigInteger()),
        sa.Column("pay_cnt_raw", sa.Text()),
        sa.Column("payer_is_known", sa.Boolean(), nullable=False),
        sa.Column("beneficiary_is_known", sa.Boolean(), nullable=False),
        sa.Column("merged_rows", sa.Integer(), nullable=False),
    )
    op.create_index("ix_payments_beneficiary", "payments", ["beneficiary"])

    op.create_table(
        "rejected_rows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("source_file", "row_number", "reason", name="uq_rejected_rows_source"),
    )
    op.create_index("ix_rejected_rows_reason", "rejected_rows", ["reason"])


def downgrade() -> None:
    op.drop_table("rejected_rows")
    op.drop_table("payments")
    op.drop_table("person_company_links")
    op.drop_table("persons")
    op.drop_table("companies")
