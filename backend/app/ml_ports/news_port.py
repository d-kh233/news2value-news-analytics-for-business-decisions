"""Источник новостей для агента: news_provider(company_id, period) -> list[dict].

Сигнатура — NewsProvider из agent/tools.py: Callable[[str, int], list[dict]], period — дни.
Каждый dict читает agent.schemas.NewsItem.from_dict. Формат элемента (согласован для B5):
    {"id": "doc-123", "title": "...", "text": "...", "published_at": "2026-10-20T07:15:00Z",
     "url": "https://...", "source": "interfax_economy", "tags": ["economy"]}
Разметки (extraction) и оценки релевантности нет — агент размечает и ранжирует сам.

Правила выборки:
  • только kind=external_news со статусом ok (аналитика банка идёт агенту другим каналом);
  • окно [today − period, today] по дням UTC, today = AGENT_TODAY (как у агента) или сегодня;
  • дата — published_at, а если источник её не дал — время сбора fetched_at;
  • сначала свежие, не больше NEWS_LIMIT;
  • company_id пока не используется: отдаём все новости периода, ранжирует агент.

Подключается к агенту в agent_port.generate_insights при NEWS_SOURCE=db (по умолчанию mock).
Агент и заглушка получают get_relevant_news — тот же news_provider под кэшем llm_cache
(CACHE_ENABLED, services/llm_cache.py). В ключ кэша входят «сегодня» и отпечаток documents,
поэтому новая новость видна при следующем refresh.
"""

import logging
import os
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.core.config import get_settings
from app.ml_ports.audit import audited
from app.models import Document
from app.services.llm_cache import cached

logger = logging.getLogger(__name__)

NEWS_LIMIT = 100


def agent_today() -> date:
    """«Сегодня» агента: AGENT_TODAY (YYYY-MM-DD), иначе текущая дата по UTC."""
    value = os.environ.get("AGENT_TODAY") or get_settings().agent_today
    return date.fromisoformat(value) if value else datetime.now(UTC).date()


def to_news_item(doc: Document) -> dict[str, Any]:
    """Строка documents -> элемент списка news_provider."""
    when = (doc.published_at or doc.fetched_at).astimezone(UTC)
    return {
        "id": f"doc-{doc.id}",
        "title": doc.title,
        "text": doc.text,
        "published_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": doc.url,
        "source": doc.source_name,
        "tags": list(doc.tags or []),
    }


def news_provider(company_id: str, period: int) -> list[dict[str, Any]]:
    """Внешние новости за последние period дней (включая сегодня), сначала свежие."""
    if period < 0:
        raise ValueError(f"period — число дней, не меньше 0, получено {period}")
    if not get_settings().database_url:
        logger.warning("news_provider: DATABASE_URL не задан — новостей нет")
        return []
    from app.core.db import get_session
    from app.repositories import document_repo

    today = agent_today()
    since = datetime.combine(today - timedelta(days=period), time.min, tzinfo=UTC)
    before = datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)
    with get_session() as session:
        docs = document_repo.recent_news(session, since, before, NEWS_LIMIT)
        items = [to_news_item(d) for d in docs]
    logger.info("news_provider(%s, %d): %d новостей из documents", company_id, period, len(items))
    return items


def _prompt_version() -> str:
    """PROMPT_VERSION генератора, которым сейчас делаются инсайты (по ML_MODE)."""
    if get_settings().ml_mode == "fake":
        from app.ml_ports import fake_port

        return fake_port.PROMPT_VERSION
    from app.ml_ports import agent_port

    return agent_port.prompt_version()


def _news_context() -> dict[str, str]:
    """Что влияет на выборку, кроме аргументов: «сегодня» и содержимое documents."""
    from app.core.db import get_session
    from app.repositories import document_repo

    with get_session() as session:
        fingerprint = document_repo.news_fingerprint(session)
    return {"today": agent_today().isoformat(), "documents": fingerprint}


def _check_news(items: list[dict[str, Any]]) -> None:
    """В кэш — только список новостей с обязательными полями (их читает NewsItem.from_dict)."""
    if not isinstance(items, list):
        raise TypeError(f"ожидался список новостей, получено {type(items).__name__}")
    for item in items:
        missing = {"id", "title", "text", "published_at"} - set(item)
        if missing:
            raise ValueError(f"новость {item.get('id')}: нет полей {sorted(missing)}")


def _news_refs(company_id: str, period: int) -> tuple[str, dict[str, Any]]:
    return company_id, {"company_id": company_id, "period_days": period}


@audited("news.get_relevant_news", _news_refs)
@cached("get_relevant_news", version=_prompt_version, validate=_check_news, context=_news_context)
def get_relevant_news(company_id: str, period: int) -> list[dict[str, Any]]:
    """news_provider под кэшем llm_cache — его получают агент и заглушка при NEWS_SOURCE=db."""
    return news_provider(company_id, period)
