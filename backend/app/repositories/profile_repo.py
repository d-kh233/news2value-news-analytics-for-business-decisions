"""Профили компаний (company_profiles) и выборки из таблиц банка для автоматической части."""

from collections.abc import Iterable
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Company, CompanyProfileRow, Payment

# buyers — кто платит компании, suppliers — кому платит компания
Side = Literal["buyers", "suppliers"]


def get(session: Session, company_id: str) -> CompanyProfileRow | None:
    return session.get(CompanyProfileRow, company_id)


def manual_data_by_ids(session: Session, company_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Ручные данные нескольких компаний одним запросом (для списка компаний)."""
    ids = list(company_ids)
    if not ids:
        return {}
    stmt = select(CompanyProfileRow.company_id, CompanyProfileRow.manual_data).where(
        CompanyProfileRow.company_id.in_(ids)
    )
    return {company_id: data for company_id, data in session.execute(stmt)}


def _upsert(session: Session, company_id: str, column: str, data: dict[str, Any]) -> None:
    stmt = insert(CompanyProfileRow).values(company_id=company_id, **{column: data})
    stmt = stmt.on_conflict_do_update(
        index_elements=[CompanyProfileRow.company_id],
        set_={column: stmt.excluded[column], "updated_at": func.now()},
    )
    session.execute(stmt)


def save_auto(session: Session, company_id: str, auto_data: dict[str, Any]) -> None:
    """Записать снимок автоматической части (ручная не меняется)."""
    _upsert(session, company_id, "auto_data", auto_data)


def save_manual(session: Session, company_id: str, manual_data: dict[str, Any]) -> None:
    """Записать ручную часть целиком (автоматическая не меняется)."""
    _upsert(session, company_id, "manual_data", manual_data)


def top_counterparties(
    session: Session, company_id: str, side: Side, limit: int = 5
) -> list[dict[str, Any]]:
    """Крупнейшие контрагенты по сумме платежей; платежи компании самой себе не считаются.

    Пары с суммой «не число» (pay_amt = NULL) идут в конце списка.
    """
    if side == "buyers":
        mine, other = Payment.beneficiary, Payment.payer
    else:
        mine, other = Payment.payer, Payment.beneficiary
    stmt = (
        select(
            other.label("company_id"),
            Payment.pay_amt.label("amount"),
            Company.synthetic_u.is_not(None).label("is_known"),
            Company.mainokved_name.label("industry"),
            CompanyProfileRow.manual_data["name"].astext.label("manual_name"),
        )
        .outerjoin(Company, Company.synthetic_u == other)
        .outerjoin(CompanyProfileRow, CompanyProfileRow.company_id == other)
        .where(mine == company_id, other != company_id)
        .order_by(Payment.pay_amt.desc().nulls_last(), other)
        .limit(limit)
    )
    return [dict(row) for row in session.execute(stmt).mappings()]


def group_size(session: Session, group_id: str) -> int:
    stmt = select(func.count()).select_from(Company).where(Company.group_id == group_id)
    return session.scalar(stmt) or 0
