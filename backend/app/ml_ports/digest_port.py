"""Текст дайджеста компании за период: llm.digest.generate_digest или заглушка (Fake).

ML_MODE=real — generate_digest из llm/ через шлюз агента (llm_call.gateway). Если модель
недоступна (LLM_MODE=stub, промах replay, нет ключа, ответ не прошёл проверку) — заглушка,
и такой ответ в кэш не кладётся: когда модель появится, дайджест пересчитается.
ML_MODE=fake — сразу заглушка: склейка заголовков самых важных инсайтов.

Под кэшем llm_cache (CACHE_ENABLED): ключ — компания, период, пары (id, важность) инсайтов и
PROMPT_VERSION (digest@v0 у модели, digest-fake@v0 у заглушки). Инсайты читаются из таблицы по id.
Аудит (audited из B8+B9) ставится строкой над @cached у digest_summary — сигнатура не меняется.
"""

import logging
from typing import Any

from app.core.config import get_settings
from app.ml_ports import llm_call
from app.services.llm_cache import cached

logger = logging.getLogger(__name__)

FAKE_VERSION = "digest-fake@v0"
PERIOD_RU = {"day": "сутки", "week": "неделю"}
FAKE_TOP = 3  # сколько заголовков склеивает заглушка


def prompt_version() -> str:
    if get_settings().ml_mode == "fake":
        return FAKE_VERSION
    llm_call.ensure_repo_on_path()
    from llm.digest import PROMPT_VERSION

    return PROMPT_VERSION


def fake_summary(period: str, insights: list[dict[str, Any]]) -> str:
    """Склейка заголовков: сколько инсайтов, рисков и возможностей, главные заголовки."""
    risks = sum(1 for i in insights if i.get("type") == "risk")
    chances = sum(1 for i in insights if i.get("type") == "opportunity")
    titles = "; ".join(i["title"].rstrip(".") for i in insights[:FAKE_TOP])
    return (
        f"За {PERIOD_RU.get(period, period)}: {len(insights)} инсайт(ов), из них рисков — {risks}, "
        f"возможностей — {chances}. Главное: {titles}."
    )


def _real_summary(company_id: str, period: str, insights: list[dict[str, Any]]) -> str:
    llm_call.ensure_repo_on_path()
    from llm.digest import generate_digest

    complete, _ = llm_call.gateway()
    digest = generate_digest(insights, llm_call.company_row(company_id), period, complete=complete)
    return f"{digest.title.rstrip('.')}. {digest.overview}"


def _load(company_id: str, ids: list[str]) -> list[dict[str, Any]]:
    from app.core.db import get_session
    from app.repositories import insight_repo

    with get_session() as session:
        rows = insight_repo.get_many(session, ids)
        return [dict(r.payload) for r in rows if r.company_id == company_id]


def _check(value: Any) -> None:
    """В кэш — только настоящий ответ: заглушку вместо недоступной модели не сохраняем."""
    if not isinstance(value, dict) or not str(value.get("summary", "")).strip():
        raise ValueError("пустой дайджест")
    if value.get("generated_by") == "fallback":
        raise ValueError("модель недоступна, ответ заглушки")


@cached("generate_digest", version=prompt_version, validate=_check)
def digest_summary(company_id: str, period: str, items: list[list[Any]]) -> dict[str, str]:
    """items — [[id инсайта, важность], ...], самые важные первыми. → {summary, generated_by}."""
    insights = _load(company_id, [i for i, _ in items])
    if get_settings().ml_mode == "fake":
        return {"summary": fake_summary(period, insights), "generated_by": "fake"}
    try:
        return {"summary": _real_summary(company_id, period, insights), "generated_by": "llm"}
    except llm_call.unavailable_errors() as exc:
        logger.warning("дайджест %s/%s: модель недоступна (%s) — заглушка", company_id, period, exc)
        return {"summary": fake_summary(period, insights), "generated_by": "fallback"}
