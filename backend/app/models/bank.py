"""Таблицы с данными банка (модуль B2).

Внешних ключей (FK) между таблицами нет намеренно: в связях и платежах много
id компаний, которых нет в companies (внешние контрагенты). Такие строки
загружаются, а флаги *_is_known / is_known_company показывают, есть ли компания.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Company(Base):
    """Юрлицо (df_Uid_data_hack.csv). Названия колонок — как в CSV, в нижнем регистре."""

    __tablename__ = "companies"

    synthetic_u: Mapped[str] = mapped_column(Text, primary_key=True)
    org_type: Mapped[str | None] = mapped_column(Text)
    okfs_name: Mapped[str | None] = mapped_column(Text)
    company_age_years_gradation: Mapped[str | None] = mapped_column(Text)
    eco_mark_label: Mapped[str | None] = mapped_column(Text)
    # код ОКВЭД — текст: бывают значения вида 46.34.2
    mainokved_code: Mapped[str | None] = mapped_column(Text)
    mainokved_name: Mapped[str | None] = mapped_column(Text)
    negativemark: Mapped[bool | None]
    amount_workers_fns_gradation: Mapped[str | None] = mapped_column(Text)
    amount_workers_spark_gradation: Mapped[str | None] = mapped_column(Text)
    cl_type: Mapped[str | None] = mapped_column(Text)
    cl_product_rko_mark: Mapped[bool | None]
    cl_product_gar_mark: Mapped[bool | None]
    cl_product_loa_mark: Mapped[bool | None]
    cl_product_lea_mark: Mapped[bool | None]
    cl_product_dep_mark: Mapped[bool | None]
    cl_product_zpp_mark: Mapped[bool | None]
    fts_flag: Mapped[bool | None]
    fts_country_name: Mapped[str | None] = mapped_column(Text)
    factoring_mark: Mapped[bool | None]
    revenue_company: Mapped[int | None] = mapped_column(BigInteger)
    # исходный текст, если в файле было не число (например «Не определено»)
    revenue_company_raw: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[str | None] = mapped_column(Text, index=True)
    city: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)


class Person(Base):
    """Физлицо (SYNTHETIC_F из файла связей). Других атрибутов в данных нет."""

    __tablename__ = "persons"

    synthetic_f: Mapped[str] = mapped_column(Text, primary_key=True)


class PersonCompanyLink(Base):
    """Связь физлица с компанией (df_Fid_Uid_link_hack.csv).

    Город и РЦ хранятся в связи: у одного человека они бывают разными для разных компаний.
    """

    __tablename__ = "person_company_links"

    synthetic_f: Mapped[str] = mapped_column(Text, primary_key=True)
    synthetic_u: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    city_name: Mapped[str | None] = mapped_column(Text)
    rc_name: Mapped[str | None] = mapped_column(Text)
    is_known_company: Mapped[bool]


class Payment(Base):
    """Платежи между компаниями (df_Uid_payment_hack.csv): одна строка на пару.

    Повторы пары в файле складываются, merged_rows — сколько строк файла сложено.
    Индекс по payer даёт первичный ключ (payer — первая колонка).
    """

    __tablename__ = "payments"

    payer: Mapped[str] = mapped_column(Text, primary_key=True)
    beneficiary: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    pay_amt: Mapped[int | None] = mapped_column(BigInteger)
    pay_amt_raw: Mapped[str | None] = mapped_column(Text)
    pay_cnt: Mapped[int | None] = mapped_column(BigInteger)
    pay_cnt_raw: Mapped[str | None] = mapped_column(Text)
    payer_is_known: Mapped[bool]
    beneficiary_is_known: Mapped[bool]
    merged_rows: Mapped[int] = mapped_column(Integer)


class RejectedRow(Base):
    """Строки CSV, которые не загружены, с причиной — чтобы их можно было проверить."""

    __tablename__ = "rejected_rows"
    __table_args__ = (
        UniqueConstraint("source_file", "row_number", "reason", name="uq_rejected_rows_source"),
        Index("ix_rejected_rows_reason", "reason"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_file: Mapped[str] = mapped_column(Text)
    # номер строки в файле, заголовок — строка 1
    row_number: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # ambiguous_id | exact_duplicate | missing_id | bad_format
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
