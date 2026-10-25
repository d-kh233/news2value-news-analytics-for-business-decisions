"""Обёртка audited: каждый вызов ML-функции — строка в audit_log, даже неудачный (B8+B9).

Ставится строкой над функцией порта (над @cached, если он есть), сами функции llm/ и agent/ не
меняются. Пишется: время, пользователь (core/context), функция (tool), компания и id входных
данных (refs — их достаёт функция-извлекатель из аргументов, тексты не пишутся), статус,
длительность, потраченные токены (их сообщает порт через core.context.add_tokens) и ошибка.

Статусы: ok; cache_hit — ответ взят из llm_cache; error — функция бросила исключение
(оно пробрасывается дальше как было); limit — дневной лимит токенов исчерпан, функция не
вызывалась (TokenLimitExceeded, см. check_token_limit).
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

from app.core.config import get_settings
from app.core.context import call_report
from app.ml_ports.base import MLError
from app.services import audit_service

logger = logging.getLogger(__name__)

# refs(*args, **kwargs) -> (company_id, {id входных данных})
Refs = Callable[..., tuple[str | None, dict[str, Any]]]


class TokenLimitExceeded(MLError):
    """Дневной лимит токенов модели (LLM_DAILY_TOKEN_LIMIT) исчерпан."""


def check_token_limit() -> None:
    """TokenLimitExceeded, если сегодня потрачено не меньше LLM_DAILY_TOKEN_LIMIT токенов.

    Лимит 0 — без лимита. Если журнал недоступен, лимит не проверяется (работа важнее счётчика).
    """
    limit = get_settings().llm_daily_token_limit
    if limit <= 0:
        return
    try:
        used = audit_service.tokens_today()
    except Exception:  # noqa: BLE001
        logger.warning("лимит токенов: журнал недоступен, проверка пропущена", exc_info=True)
        return
    if used >= limit:
        raise TokenLimitExceeded(
            f"Дневной лимит токенов модели исчерпан ({used} из {limit}, LLM_DAILY_TOKEN_LIMIT). "
            "Повторите завтра или временно переключите ML_MODE=fake"
        )


def _safe_refs(refs: Refs | None, args: tuple, kwargs: dict) -> tuple[str | None, dict[str, Any]]:
    if refs is None:
        return None, {}
    try:
        return refs(*args, **kwargs)
    except Exception:  # noqa: BLE001 — из-за журнала вызов не должен падать
        logger.warning("audit: не удалось достать id входных данных", exc_info=True)
        return None, {}


def audited(
    tool: str, refs: Refs | None = None, *, uses_llm: Callable[[], bool] | bool = False
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """tool — имя в журнале; refs — извлекатель id; uses_llm — тратит ли вызов токены модели
    (тогда перед вызовом проверяется дневной лимит). Можно передать функцию: режим (ML_MODE)
    узнаётся в момент вызова."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            company_id, input_refs = _safe_refs(refs, args, kwargs)
            t0 = time.perf_counter()
            with call_report() as report:
                try:
                    if uses_llm() if callable(uses_llm) else uses_llm:
                        check_token_limit()
                    result = fn(*args, **kwargs)
                except TokenLimitExceeded as exc:
                    _write(tool, "limit", company_id, input_refs, t0, report.tokens, str(exc))
                    raise
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    _write(tool, "error", company_id, input_refs, t0, report.tokens, error)
                    raise
            status = "cache_hit" if report.cache_hit else "ok"
            _write(tool, status, company_id, input_refs, t0, report.tokens, None)
            return result

        return wrapper

    return decorate


def _write(
    tool: str,
    status: str,
    company_id: str | None,
    input_refs: dict[str, Any],
    t0: float,
    tokens: int | None,
    error: str | None,
) -> None:
    audit_service.record(
        "ml_call",
        status,
        tool=tool,
        company_id=company_id,
        input_refs=input_refs,
        duration_ms=audit_service.elapsed_ms(t0),
        tokens_used=tokens,
        error=error,
    )


def real_mode() -> bool:
    """Для uses_llm: модель может вызываться только в ML_MODE=real."""
    return get_settings().ml_mode == "real"
