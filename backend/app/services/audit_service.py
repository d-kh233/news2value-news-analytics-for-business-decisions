"""Запись в журнал audit_log (B8+B9) и дневной счётчик токенов.

Журнал не должен ломать основную работу: если запись не удалась (нет БД), это предупреждение
в лог, а вызывающий продолжает. Тексты в журнал не пишутся — только id и короткая ошибка.
"""

import logging
import re
import time
from datetime import UTC, datetime
from datetime import time as dtime
from typing import Any

from app.core.auth import User
from app.core.context import get_user_id
from app.core.db import get_session
from app.repositories import audit_repo

logger = logging.getLogger(__name__)

ERROR_MAX_LEN = 500
_EMAIL = re.compile(r"^([^@]?)[^@]*@([^.]?)[^.]*(\..*)?$")


def mask_email(email: str) -> str:
    """d.kh@gmail.com -> d***@g***.com: по маске видно, что за попытка, но не сам адрес."""
    m = _EMAIL.match(email.strip().lower())
    if not m:
        return "***"
    return f"{m.group(1)}***@{m.group(2)}***{m.group(3) or ''}"


def elapsed_ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def record(
    action: str,
    status: str,
    *,
    tool: str | None = None,
    company_id: str | None = None,
    input_refs: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    tokens_used: int | None = None,
    error: str | None = None,
    user_id: str | None = None,
) -> None:
    from app.core.logging import mask_secrets

    values = {
        "user_id": user_id if user_id is not None else get_user_id(),
        "action": action,
        "tool": tool,
        "company_id": company_id,
        "input_refs": input_refs or {},
        "status": status,
        "duration_ms": duration_ms,
        "tokens_used": tokens_used,
        "error": mask_secrets(error)[:ERROR_MAX_LEN] if error else None,
    }
    try:
        with get_session() as session:
            audit_repo.add(session, values)
            session.commit()
    except Exception:  # noqa: BLE001 — журнал не ломает основную работу
        logger.warning("audit_log: запись %s/%s не сохранена", action, tool, exc_info=True)


def record_login(
    user: User | None, email: str, status: str, t0: float, error: str | None = None
) -> None:
    company_id = None
    if user is not None and user.company_ids is not None and len(user.company_ids) == 1:
        company_id = next(iter(user.company_ids))
    refs = {"user_id": user.id} if user is not None else {"email": mask_email(email)}
    record(
        "login",
        status,
        tool="auth.login",
        company_id=company_id,
        input_refs=refs,
        duration_ms=elapsed_ms(t0),
        error=error,
        user_id=user.id if user is not None else None,
    )


def tokens_today() -> int:
    """Токены модели с полуночи UTC по журналу."""
    since = datetime.combine(datetime.now(UTC).date(), dtime.min, tzinfo=UTC)
    with get_session() as session:
        return audit_repo.tokens_since(session, since)
