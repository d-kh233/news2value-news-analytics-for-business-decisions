"""Ответ на вопрос по инсайту: llm.qa.answer_question или заглушка (Fake).

answer_question(insight, question, history) -> {answer, sources[], generated_by}
  insight — карточка API (dict, с источниками из documents); history — [{question, answer}],
  старые первыми; sources — в схеме Source (только источники самого инсайта).

ML_MODE=real — answer_question из llm/ через шлюз агента (llm_call.gateway). Модель недоступна
(LLM_MODE=stub, промах replay, нет ключа, ответ не прошёл проверку) — заглушка, в кэш не кладётся.
ML_MODE=fake — сразу заглушка: ответ-шаблон и все источники инсайта.

Под кэшем llm_cache (CACHE_ENABLED): ключ — карточка, вопрос, история и PROMPT_VERSION
(qa@v0 у модели, qa-fake@v0 у заглушки). Каждый вызов пишется в audit_log (audited, B8+B9);
дневной лимит токенов исчерпан — TokenLimitExceeded: задача ask получает failed с пояснением.
"""

import logging
from typing import Any

from app.core.config import get_settings
from app.core.context import add_tokens
from app.ml_ports import llm_call
from app.ml_ports.audit import audited, real_mode
from app.services.llm_cache import cached

logger = logging.getLogger(__name__)

FAKE_VERSION = "qa-fake@v0"


def prompt_version() -> str:
    if get_settings().ml_mode == "fake":
        return FAKE_VERSION
    llm_call.ensure_repo_on_path()
    from llm.qa import PROMPT_VERSION

    return PROMPT_VERSION


def fake_answer(insight: dict[str, Any], question: str) -> dict[str, Any]:
    """Ответ-шаблон по полям инсайта и все его источники."""
    action = str(insight.get("recommended_action") or "").strip().rstrip(".")
    answer = (
        f"По теме «{insight.get('title', '')}»: {action}. "
        f"Вопрос был: «{question}». Это демонстрационный ответ без модели."
    )
    return {"answer": answer, "sources": list(insight.get("sources") or [])}


def _real_answer(
    insight: dict[str, Any], question: str, history: list[dict[str, Any]]
) -> dict[str, Any]:
    llm_call.ensure_repo_on_path()
    from llm.qa import answer_question as llm_answer
    from llm.qa import answer_sources

    complete, _ = llm_call.gateway()
    company = llm_call.company_row(insight.get("company_id", ""))
    try:
        ans = llm_answer(insight, question, history, company_profile=company, complete=complete)
    finally:
        add_tokens(llm_call.tokens_spent(complete))
    return {"answer": ans.answer, "sources": answer_sources(insight, ans)}


def _check(value: Any) -> None:
    """В кэш — только настоящий ответ: заглушку вместо недоступной модели не сохраняем."""
    if not isinstance(value, dict) or not str(value.get("answer", "")).strip():
        raise ValueError("пустой ответ")
    if value.get("generated_by") == "fallback":
        raise ValueError("модель недоступна, ответ заглушки")


def _refs(insight: dict[str, Any], question: str, history: list[dict[str, Any]]):
    company_id = insight.get("company_id")
    return company_id, {"insight_id": insight.get("id"), "history_len": len(history)}


@audited("llm.answer_question", _refs, uses_llm=real_mode)
@cached("answer_question", version=prompt_version, validate=_check)
def answer_question(
    insight: dict[str, Any], question: str, history: list[dict[str, Any]]
) -> dict[str, Any]:
    if get_settings().ml_mode == "fake":
        return {**fake_answer(insight, question), "generated_by": "fake"}
    try:
        return {**_real_answer(insight, question, history), "generated_by": "llm"}
    except llm_call.unavailable_errors() as exc:
        logger.warning(
            "ответ на вопрос по %s: модель недоступна (%s) — заглушка", insight.get("id"), exc
        )
        return {**fake_answer(insight, question), "generated_by": "fallback"}
