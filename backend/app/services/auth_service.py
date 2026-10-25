"""Вход по email и паролю (B8+B9): проверка пароля, токен, запись в журнал audit_log.

Регистрации, восстановления пароля и OAuth нет: пользователей создаёт
scripts/create_demo_users.py. На неверный email и неверный пароль ответ одинаковый (401),
чтобы по ответу нельзя было узнать, есть ли такой пользователь.
"""

import logging
import time

from app.core.auth import User, user_from_row
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import ServiceUnavailableError, UnauthorizedError
from app.core.security import SecretMissing, create_token, hash_password, verify_password
from app.repositories import user_repo
from app.schemas.auth import LoginResponse, UserInfo
from app.services import audit_service

logger = logging.getLogger(__name__)

BAD_LOGIN = "Неверный email или пароль"
# хеш-пустышка: при неизвестном email всё равно считаем bcrypt, чтобы ответ шёл столько же
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def user_info(user: User) -> UserInfo:
    company_id = None
    if user.company_ids is not None and len(user.company_ids) == 1:
        company_id = next(iter(user.company_ids))
    return UserInfo(
        id=user.id,
        email=user.email,
        role=user.role,  # type: ignore[arg-type]
        company_id=company_id,
        is_admin=user.is_admin,
        auth_enabled=get_settings().auth_enabled,
    )


def login(email: str, password: str) -> LoginResponse:
    t0 = time.perf_counter()
    with get_session() as session:
        row = user_repo.get_by_email(session, email)
        ok = verify_password(password, row.password_hash if row else _DUMMY_HASH) and row
        user = user_from_row(row) if ok else None
    if user is None:
        audit_service.record_login(None, email, "failed", t0, BAD_LOGIN)
        logger.info("вход: неудача (%s)", email)
        raise UnauthorizedError(BAD_LOGIN)
    try:
        token, ttl = create_token(int(user.id))
    except SecretMissing as exc:
        audit_service.record_login(user, email, "error", t0, str(exc))
        raise ServiceUnavailableError(str(exc)) from exc
    audit_service.record_login(user, email, "ok", t0)
    logger.info("вход: пользователь %s (%s)", user.id, user.role)
    return LoginResponse(access_token=token, expires_in=ttl, user=user_info(user))
