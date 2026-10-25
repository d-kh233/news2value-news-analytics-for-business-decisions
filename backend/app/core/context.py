"""Что известно о текущей работе: кто её делает и что сообщил вызов ML (для журнала audit_log).

contextvars — переменные, у каждого запроса и каждой задачи worker'а своё значение.
  • user_id: API ставит в current_user, worker — из jobs.params.user_id перед задачей;
    фоновые скрипты не ставят — в журнале user_id пустой.
  • отчёт вызова (CallReport): обёртка audited (app/ml_ports/audit.py) открывает его перед
    вызовом ML-функции; функция сообщает потраченные токены (add_tokens), кэш — что ответ
    взят из llm_cache (mark_cache_hit). Вложенный вызов открывает свой отчёт, поэтому токены
    не считаются дважды.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)


def set_user_id(user_id: str | None) -> None:
    _user_id.set(user_id)


def get_user_id() -> str | None:
    return _user_id.get()


@contextmanager
def acting_user(user_id: str | None) -> Iterator[None]:
    """user_id на время блока (задача worker'а), потом прежнее значение."""
    token = _user_id.set(user_id)
    try:
        yield
    finally:
        _user_id.reset(token)


@dataclass
class CallReport:
    tokens: int | None = None
    cache_hit: bool = False


_report: ContextVar[CallReport | None] = ContextVar("call_report", default=None)


@contextmanager
def call_report() -> Iterator[CallReport]:
    report = CallReport()
    token = _report.set(report)
    try:
        yield report
    finally:
        _report.reset(token)


def add_tokens(n: int | None) -> None:
    """Сообщить, сколько токенов модели потратил текущий вызов (None/0 — не тратил)."""
    report = _report.get()
    if report is not None and n:
        report.tokens = (report.tokens or 0) + int(n)


def mark_cache_hit() -> None:
    report = _report.get()
    if report is not None:
        report.cache_hit = True
