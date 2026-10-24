"""Инсайты компаний (таблица insights): замена при refresh и чтение."""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import InsightRow


def replace_for_company(session: Session, company_id: str, rows: list[dict[str, Any]]) -> int:
    """Удалить все инсайты компании и вставить новые; вернуть число удалённых.

    Коммит делает вызывающий: удаление и вставка идут в одной транзакции, поэтому при сбое
    вставки старые инсайты остаются.
    """
    removed = session.execute(delete(InsightRow).where(InsightRow.company_id == company_id))
    session.add_all(InsightRow(company_id=company_id, **row) for row in rows)
    session.flush()
    return removed.rowcount or 0


def list_for_company(session: Session, company_id: str) -> list[InsightRow]:
    """Инсайты компании: сначала важные, при равенстве — по id."""
    stmt = (
        select(InsightRow)
        .where(InsightRow.company_id == company_id)
        .order_by(InsightRow.importance.desc(), InsightRow.id)
    )
    return list(session.scalars(stmt))
