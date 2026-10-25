"""Инсайты компаний (таблица insights): замена при refresh и чтение для API."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.models import InsightRow

# инсайт ссылается на документ с этим тегом (теги documents — JSONB-список; регистр не важен)
_HAS_TAG = sql_text(
    "EXISTS (SELECT 1 FROM documents d, jsonb_array_elements_text(d.tags) AS t(tag) "
    "WHERE d.id = ANY(insights.document_ids) AND lower(t.tag) = :industry)"
)


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


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def search(
    session: Session,
    company_id: str,
    *,
    insight_type: str | None = None,
    min_importance: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    industry: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[InsightRow], int]:
    """Страница ленты и общее число найденных.

    Даты — день created_at по UTC, обе границы включительно. industry — тег документа-источника
    (okved:41, economy…), без учёта регистра. Сортировка: важность ↓, created_at ↓, id.
    """
    stmt = select(InsightRow).where(InsightRow.company_id == company_id)
    if insight_type is not None:
        stmt = stmt.where(InsightRow.type == insight_type)
    if min_importance is not None:
        stmt = stmt.where(InsightRow.importance >= min_importance)
    if date_from is not None:
        stmt = stmt.where(InsightRow.created_at >= _day_start(date_from))
    if date_to is not None:
        stmt = stmt.where(InsightRow.created_at < _day_start(date_to + timedelta(days=1)))
    if industry is not None:
        stmt = stmt.where(_HAS_TAG.bindparams(industry=industry.lower()))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = (
        stmt.order_by(InsightRow.importance.desc(), InsightRow.created_at.desc(), InsightRow.id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(page)), total


def count_for_company(session: Session, company_id: str) -> int:
    return session.scalar(select(func.count()).where(InsightRow.company_id == company_id)) or 0


def get(session: Session, insight_id: str) -> InsightRow | None:
    return session.get(InsightRow, insight_id)


def get_many(session: Session, insight_ids: list[str]) -> list[InsightRow]:
    """Инсайты по id в том же порядке; отсутствующих в ответе нет."""
    if not insight_ids:
        return []
    found = {
        r.id: r for r in session.scalars(select(InsightRow).where(InsightRow.id.in_(insight_ids)))
    }
    return [found[i] for i in insight_ids if i in found]


def top_since(session: Session, company_id: str, since: datetime, limit: int) -> list[InsightRow]:
    """Самые важные инсайты компании, созданные не раньше since (для дайджеста)."""
    stmt = (
        select(InsightRow)
        .where(InsightRow.company_id == company_id, InsightRow.created_at >= since)
        .order_by(InsightRow.importance.desc(), InsightRow.created_at.desc(), InsightRow.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))
