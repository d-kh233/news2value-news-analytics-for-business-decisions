"""Автоматическая часть профиля компании из таблиц банка (B2).

Пустые данные — это нормально: поле остаётся пустым ("" / [] / None), ошибки нет.
Ключи результата — имена полей CompanyProfile; значения — готовый JSON для auto_data.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.models import Company
from app.repositories import profile_repo

TOP_COUNTERPARTIES = 5

# флаг продукта в companies -> название продукта для профиля
BANK_PRODUCTS = (
    ("cl_product_rko_mark", "РКО"),  # расчётно-кассовое обслуживание
    ("cl_product_gar_mark", "Гарантии"),
    ("cl_product_loa_mark", "Кредиты"),
    ("cl_product_lea_mark", "Лизинг"),
    ("cl_product_dep_mark", "Депозиты"),
    ("cl_product_zpp_mark", "Зарплатный проект"),
    ("factoring_mark", "Факторинг"),
)


def display_name(company_id: str, manual_name: str | None) -> str:
    """Название компании: ручное (manual_data.name), иначе её id (U_67)."""
    return manual_name or company_id


def _countries(value: str | None) -> list[str]:
    """«КИТАЙ, ТУРЦИЯ» -> ["КИТАЙ", "ТУРЦИЯ"]."""
    return [c.strip() for c in (value or "").split(",") if c.strip()]


def _counterparties(session: Session, company_id: str, side: profile_repo.Side) -> list[dict]:
    rows = profile_repo.top_counterparties(session, company_id, side, TOP_COUNTERPARTIES)
    return [
        {
            "company_id": r["company_id"],
            "name": display_name(r["company_id"], r["manual_name"]),
            "industry": r["industry"] or "",
            "amount": r["amount"],
            "is_known": r["is_known"],
        }
        for r in rows
    ]


def build_auto(session: Session, company: Company) -> dict[str, Any]:
    """Профиль по данным банка: отрасль, регион, масштаб, продукты, ВЭД, группа, контрагенты."""
    buyers = _counterparties(session, company.synthetic_u, "buyers")
    suppliers = _counterparties(session, company.synthetic_u, "suppliers")
    group_size = profile_repo.group_size(session, company.group_id) if company.group_id else 0
    revenue = company.revenue_company
    return {
        "name": company.synthetic_u,
        "industry": company.mainokved_name or "",
        "okved_code": company.mainokved_code or "",
        "regions": [company.region] if company.region else [],
        "city": company.city or "",
        # значение как в файле, без пересчёта единиц
        "revenue_bln_rub": float(revenue) if revenue is not None else 0.0,
        "revenue_known": company.revenue_known,
        "employees": company.amount_workers_fns_gradation
        or company.amount_workers_spark_gradation
        or "",
        "bank_products": [name for flag, name in BANK_PRODUCTS if getattr(company, flag)],
        "foreign_trade": company.fts_flag,
        "foreign_trade_countries": _countries(company.fts_country_name),
        "group_id": company.group_id or "",
        "group_size": group_size,
        "top_buyers": buyers,
        "top_suppliers": suppliers,
        "key_clients": [c["name"] for c in buyers],
        "key_suppliers": [c["name"] for c in suppliers],
    }
