"""Адаптеры ML_MODE=real (B8+B9): функции agent/ и llm/ через существующие порты + таймаут,
один повтор и единая ошибка MLError.

Сами функции ML не меняются, пересчёта форматов здесь нет — его делают порты:
  generate_insights → agent_port.generate_insights (агент agent/orchestrator.py);
  digest_summary    → digest_port.digest_summary (llm/digest.py);
  answer_question   → qa_port.answer_question (llm/qa.py).
Кэш (llm_cache) и журнал (audit_log) стоят на функциях портов, поэтому работают и здесь;
каждая попытка — своя строка в журнале.

Таймаут: ML_TIMEOUT_SECONDS (60) на дайджест и ответ, ML_AGENT_TIMEOUT_SECONDS (300) на
прогон агента — у него несколько вызовов модели по 90 с каждый (agent/config.py). Функция
выполняется в отдельном потоке; поток в Python нельзя остановить, поэтому после таймаута он
доработает в фоне, а его результат выбрасывается.

Повтор: ML_RETRIES (1) раз при любой ошибке, кроме тех, где повтор ничего не изменит: лимит
токенов, неизвестная компания (LookupError), неверные параметры (ValueError).
"""

import concurrent.futures
import contextvars
import logging
from collections.abc import Callable
from typing import Any

from app.core.config import get_settings
from app.ml_ports import agent_port, digest_port, qa_port
from app.ml_ports.base import MLError

logger = logging.getLogger(__name__)

# повтор этих ошибок ничего не изменит
NO_RETRY: tuple[type[Exception], ...] = (LookupError, ValueError, MLError)


def _run_with_timeout(fn: Callable[..., Any], timeout: float, args: tuple, kwargs: dict) -> Any:
    # copy_context: в потоке видны пользователь и отчёт вызова для журнала (core/context)
    ctx = contextvars.copy_context()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ml")
    future = pool.submit(ctx.run, fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    finally:
        # не ждём зависший поток: он доработает сам, результат никто не прочитает
        pool.shutdown(wait=False, cancel_futures=True)


def call(tool: str, fn: Callable[..., Any], timeout: float, *args: Any, **kwargs: Any) -> Any:
    """Вызвать fn с таймаутом и повтором; любая ошибка — MLError с понятным текстом."""
    attempts = 1 + max(0, get_settings().ml_retries)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _run_with_timeout(fn, timeout, args, kwargs)
        except concurrent.futures.TimeoutError:
            last = TimeoutError(f"нет ответа за {timeout:g} с")
        except NO_RETRY as exc:
            if isinstance(exc, MLError):
                raise
            raise MLError(f"{tool}: {type(exc).__name__}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — любая ошибка ML превращается в MLError
            last = exc
        if attempt < attempts:
            logger.warning("%s: попытка %d не удалась (%s), повторяю", tool, attempt, last)
    raise MLError(f"{tool}: {type(last).__name__}: {last} (попыток: {attempts})") from last


def generate_insights(
    company_id: str, role: str | None = None, *, period: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    timeout = get_settings().ml_agent_timeout_seconds
    # agent_port.generate_insights берётся при вызове: в тестах его подменяют
    return call(
        "agent.generate_insights",
        agent_port.generate_insights,
        timeout,
        company_id,
        role,
        period=period,
    )


def digest_summary(company_id: str, period: str, items: list[list[Any]]) -> dict[str, str]:
    timeout = get_settings().ml_timeout_seconds
    return call(
        "llm.generate_digest", digest_port.digest_summary, timeout, company_id, period, items
    )


def answer_question(
    insight: dict[str, Any], question: str, history: list[dict[str, Any]]
) -> dict[str, Any]:
    timeout = get_settings().ml_timeout_seconds
    return call("llm.answer_question", qa_port.answer_question, timeout, insight, question, history)
