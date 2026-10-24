"""Кэш ответов генератора в таблице llm_cache (B6): обёртка cached.

Ключ — sha256 от имени функции, входных данных (args, kwargs), контекста и PROMPT_VERSION.
Контекст — то, что функция берёт не из аргументов, а из окружения: например, «сегодня» и
отпечаток таблицы documents у get_relevant_news. Без него кэш отдавал бы устаревший ответ.
Смена PROMPT_VERSION меняет все ключи, поэтому старые ответы больше не находятся.

Включается CACHE_ENABLED=1; без неё обёртка просто вызывает функцию, как раньше.
Ответ, который не прошёл validate, в кэш не кладётся (вызывающий получает его как есть).
Если кэш недоступен (нет БД), функция вызывается напрямую: генерация из-за кэша не ломается.

Где стоит: generate_insight и get_relevant_news в app/ml_ports (fake_port, news_port).
Код agent/ и llm/ не трогаем; вызовы модели внутри агента кэширует его шлюз (agent/gateway.py).
"""

import functools
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

from app.core.config import get_settings
from app.core.db import get_session
from app.repositories import llm_cache_repo

logger = logging.getLogger(__name__)


def make_key(name: str, args: Any, kwargs: Any, context: Any, version: str) -> str:
    """sha256 от имени функции, входных данных, контекста и PROMPT_VERSION."""
    raw = json.dumps(
        {"fn": name, "args": args, "kwargs": kwargs, "ctx": context, "v": version},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _take(key: str) -> Any | None:
    with get_session() as session:
        value = llm_cache_repo.take(session, key)
        session.commit()
    return value


def _put(key: str, value: Any) -> None:
    with get_session() as session:
        llm_cache_repo.put(session, key, value)
        session.commit()


def cached(
    name: str,
    *,
    version: Callable[[], str],
    validate: Callable[[Any], object],
    context: Callable[[], Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Обёртка: ответ функции берётся из llm_cache, если такой вызов уже был.

    version() — текущий PROMPT_VERSION, validate(ответ) бросает исключение, если ответ
    нельзя сохранять, context() — данные окружения для ключа. Ответ должен быть JSON.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not get_settings().cache_enabled:
                return fn(*args, **kwargs)
            try:
                ctx = context() if context is not None else None
                key = make_key(name, list(args), kwargs, ctx, version())
                hit = _take(key)
            except Exception:  # noqa: BLE001 — без кэша работаем как раньше
                logger.warning("кэш %s недоступен — вызов без кэша", name, exc_info=True)
                return fn(*args, **kwargs)
            if hit is not None:
                logger.debug("кэш %s: попадание %s", name, key[:12])
                return hit

            value = fn(*args, **kwargs)
            try:
                validate(value)
            except Exception as exc:  # noqa: BLE001 — любой непрошедший проверку ответ
                logger.warning("кэш %s: ответ не прошёл проверку, не сохраняем: %s", name, exc)
                return value
            try:
                _put(key, value)
            except Exception:  # noqa: BLE001 — не сохранили, но ответ у вызывающего есть
                logger.warning("кэш %s: не удалось сохранить ответ", name, exc_info=True)
            return value

        return wrapper

    return decorate
