"""Инсайты для API (B7): лента, карточка, дайджест — из таблицы insights или из моков.

USE_MOCKS=true — всё как до B7 (services/mock_store.py, сортировка по дате): запасной вариант
демо. Иначе — БД: лента и дайджест читают insights, карточка дополняет источники данными из
documents (по document_ids). Роуты зовут только этот модуль.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import NotFoundError
from app.ml_ports import digest_port, tagging_port
from app.models import Document
from app.repositories import document_repo, insight_repo, job_repo
from app.schemas.digest import Digest, DigestPeriod
from app.schemas.insight import Insight, InsightPage, InsightType
from app.services import company_service

HINT_EMPTY = "Инсайты ещё не собраны, нажмите «Обновить»"
HINT_RUNNING = "Инсайты собираются…"
DIGEST_EMPTY = "Новых инсайтов нет."
DIGEST_TOP = 10
PERIOD_DAYS = {"day": 1, "week": 7}
REFRESH_KIND = "refresh"


def use_mocks() -> bool:
    return get_settings().use_mocks


def _mocks() -> Any:
    # mock_store при импорте читает data/mocks — без USE_MOCKS он не нужен
    from app.services import mock_store

    return mock_store


def _require_company(company_id: str) -> None:
    if not company_service.company_exists(company_id):
        raise NotFoundError(f"Компания {company_id} не найдена")


# ---- лента ----


def list_insights(
    company_id: str,
    *,
    insight_type: InsightType | None,
    min_importance: int | None,
    date_from: date | None,
    date_to: date | None,
    industry: str | None,
    limit: int,
    offset: int,
) -> InsightPage:
    if use_mocks():
        items, total = _mocks().list_insights(
            company_id,
            insight_type=insight_type,
            min_importance=min_importance,
            date_from=date_from,
            date_to=date_to,
            industry=industry,
            limit=limit,
            offset=offset,
        )
        return InsightPage(items=items, total=total, limit=limit, offset=offset)

    _require_company(company_id)
    tag = tagging_port.normalize_industry(industry) if industry else None
    with get_session() as session:
        rows, total = insight_repo.search(
            session,
            company_id,
            insight_type=insight_type,
            min_importance=min_importance,
            date_from=date_from,
            date_to=date_to,
            industry=tag,
            limit=limit,
            offset=offset,
        )
        items = [Insight.model_validate(r.payload) for r in rows]
        job_id = job_repo.active_id(session, company_id, REFRESH_KIND)
        has_any = total > 0 or insight_repo.count_for_company(session, company_id) > 0
    hint = None
    if not has_any:
        hint = HINT_RUNNING if job_id else HINT_EMPTY
    return InsightPage(
        items=items, total=total, limit=limit, offset=offset, hint=hint, refresh_job_id=job_id
    )


# ---- карточка ----


def _doc_source(doc: Document) -> dict[str, Any]:
    when = (doc.published_at or doc.fetched_at).astimezone(UTC)
    return {
        "id": f"N:doc-{doc.id}",
        "title": doc.title,
        "url": doc.url or "",
        "published_at": when,
        "source_kind": "external" if doc.kind == "external_news" else "bank_analytics",
    }


def with_document_sources(payload: dict[str, Any], docs: list[Document]) -> dict[str, Any]:
    """Источники карточки: совпавшие с документом (по id N:doc-<id> или по url) — данными
    документа, недостающие документы — в конец, прочие (моковые новости, тезисы банка) — как
    были."""
    fresh = [_doc_source(d) for d in docs]
    by_id = {s["id"]: s for s in fresh}
    by_url = {s["url"]: s for s in fresh if s["url"]}
    used: set[str] = set()
    sources = []
    for src in payload.get("sources") or []:
        match = by_id.get(src.get("id") or "") or (
            by_url.get(src.get("url")) if not src.get("id") else None
        )
        if match is not None and match["id"] not in used:
            sources.append(match)
            used.add(match["id"])
        elif match is None:
            sources.append(src)
    sources += [s for s in fresh if s["id"] not in used]
    return {**payload, "sources": sources}


def _db_insight(insight_id: str) -> Insight:
    with get_session() as session:
        row = insight_repo.get(session, insight_id)
        if row is None:
            raise NotFoundError(f"Инсайт {insight_id} не найден")
        found = document_repo.get_many(session, list(row.document_ids or []))
        docs = [found[i] for i in row.document_ids or [] if i in found]
        return Insight.model_validate(with_document_sources(dict(row.payload), docs))


def get_insight(insight_id: str) -> Insight:
    if use_mocks():
        return _mocks().get_insight(insight_id)
    return _db_insight(insight_id)


def db_insight_payload(insight_id: str) -> dict[str, Any]:
    """Карточка из БД как JSON (для ответа на вопрос в worker) — с источниками из documents."""
    return _db_insight(insight_id).model_dump(mode="json")


# ---- дайджест ----


def get_digest(company_id: str, period: DigestPeriod) -> Digest:
    if use_mocks():
        return _mocks().get_digest(company_id, period)
    _require_company(company_id)
    since = datetime.now(UTC) - timedelta(days=PERIOD_DAYS[period])
    with get_session() as session:
        rows = insight_repo.top_since(session, company_id, since, DIGEST_TOP)
        items = [[r.id, r.importance] for r in rows]
    if not items:
        return Digest(company_id=company_id, period=period, summary=DIGEST_EMPTY, insight_ids=[])
    result = digest_port.digest_summary(company_id, period, items)
    return Digest(
        company_id=company_id,
        period=period,
        summary=result["summary"],
        insight_ids=[i for i, _ in items],
    )
