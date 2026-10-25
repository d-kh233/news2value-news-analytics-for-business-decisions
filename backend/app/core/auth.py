"""Текущий пользователь и права (B8+B9).

AUTH_ENABLED=false (по умолчанию): все запросы — от анонимного пользователя demo, он
администратор и видит все компании (как до B8+B9).
AUTH_ENABLED=true: нужен заголовок Authorization: Bearer <токен из POST /auth/login>.
Нет токена, токен неверный или истёк, пользователь удалён — 401. Права:
  • admin видит все компании и служебные эндпоинты (/documents, /feedback/stats);
  • остальные роли (owner, ceo, cfo, treasurer) — только свою компанию, иначе 403.
В тестах пользователь подменяется через app.dependency_overrides[current_user].
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError

DEMO_USER_ID = "demo"
ADMIN_ROLE = "admin"


@dataclass(frozen=True)
class User:
    id: str
    is_admin: bool = False
    # компании, которые пользователь видит; None — все
    company_ids: frozenset[str] | None = None
    role: str | None = None
    email: str | None = None

    def can_see(self, company_id: str) -> bool:
        return self.company_ids is None or company_id in self.company_ids


DEMO_USER = User(id=DEMO_USER_ID, is_admin=True, company_ids=None, role=ADMIN_ROLE)

# auto_error=False: без заголовка FastAPI не отвечает сам, решаем здесь (401 в нашей форме)
_bearer = HTTPBearer(auto_error=False, description="Токен из POST /auth/login")


def user_from_row(row: object) -> User:
    role = row.role  # type: ignore[attr-defined]
    company_id = row.company_id  # type: ignore[attr-defined]
    is_admin = role == ADMIN_ROLE
    return User(
        id=str(row.id),  # type: ignore[attr-defined]
        is_admin=is_admin,
        company_ids=None if is_admin else frozenset({company_id}),
        role=role,
        email=row.email,  # type: ignore[attr-defined]
    )


def _user_by_token(token: str) -> User:
    from app.core.db import get_session
    from app.core.security import SecretMissing, TokenError, decode_token
    from app.repositories import user_repo

    try:
        user_id = decode_token(token)
    except (TokenError, SecretMissing) as exc:
        raise UnauthorizedError("Токен неверный или истёк — войдите заново") from exc
    with get_session() as session:
        row = user_repo.get(session, user_id)
        if row is None:
            raise UnauthorizedError("Пользователь не найден — войдите заново")
        return user_from_row(row)


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    """Зависимость FastAPI: кто делает запрос.

    async — чтобы id пользователя для журнала (core/context) был виден в роуте: синхронная
    зависимость работает в отдельном потоке, и её contextvars до роута не доходят.
    Чтение пользователя из БД — в потоке (run_in_threadpool), цикл событий не блокируется.
    """
    from starlette.concurrency import run_in_threadpool

    from app.core.context import set_user_id

    if not get_settings().auth_enabled:
        user = DEMO_USER
    elif credentials is None or not credentials.credentials:
        raise UnauthorizedError("Нужен вход: заголовок Authorization: Bearer <токен>")
    else:
        user = await run_in_threadpool(_user_by_token, credentials.credentials)
    set_user_id(user.id)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def ensure_can_see(user: User, company_id: str) -> None:
    """403, если инсайт или компания пользователю не доступны."""
    if not user.can_see(company_id):
        raise ForbiddenError("Нет доступа к этой компании")


def ensure_admin(user: User) -> None:
    if not user.is_admin:
        raise ForbiddenError("Доступно только администратору")


def company_user(company_id: str, user: CurrentUser) -> User:
    """Зависимость для /companies/{company_id}/...: пользователь с правом на эту компанию."""
    ensure_can_see(user, company_id)
    return user


def admin_user(user: CurrentUser) -> User:
    """Зависимость для служебных эндпоинтов: только admin."""
    ensure_admin(user)
    return user


CompanyUser = Annotated[User, Depends(company_user)]
AdminUser = Annotated[User, Depends(admin_user)]
