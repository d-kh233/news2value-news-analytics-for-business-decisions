"""Текущий пользователь и права — временная заготовка до модуля B8+B9 (не авторизация).

AUTH_ENABLED=false (по умолчанию): все запросы — от анонимного пользователя demo, он
администратор и видит все компании. Так работают ask, feedback и /feedback/stats в /docs.
AUTH_ENABLED=true: входа ещё нет (OAuth, пароли, токены — B8+B9), поэтому 401. В тестах
пользователь подменяется через app.dependency_overrides[current_user].

B8+B9 заменяет только тело current_user (откуда брать пользователя); User и ensure_can_see
остаются, роуты не меняются.
"""

from dataclasses import dataclass

from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError

DEMO_USER_ID = "demo"


@dataclass(frozen=True)
class User:
    id: str
    is_admin: bool = False
    # компании, которые пользователь видит; None — все
    company_ids: frozenset[str] | None = None

    def can_see(self, company_id: str) -> bool:
        return self.company_ids is None or company_id in self.company_ids


DEMO_USER = User(id=DEMO_USER_ID, is_admin=True, company_ids=None)


def current_user() -> User:
    """Зависимость FastAPI: кто делает запрос."""
    if not get_settings().auth_enabled:
        return DEMO_USER
    raise UnauthorizedError(
        "Авторизация включена (AUTH_ENABLED=true), но вход появится в модуле B8+B9"
    )


def ensure_can_see(user: User, company_id: str) -> None:
    """403, если инсайт или компания пользователю не доступны."""
    if not user.can_see(company_id):
        raise ForbiddenError("Нет доступа к инсайтам этой компании")


def ensure_admin(user: User) -> None:
    if not user.is_admin:
        raise ForbiddenError("Доступно только администратору")
