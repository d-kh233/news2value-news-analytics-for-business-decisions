from typing import Literal

from pydantic import BaseModel, Field

UserRole = Literal["owner", "ceo", "cfo", "treasurer", "admin"]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class UserInfo(BaseModel):
    id: str
    email: str | None = None
    role: UserRole | None = None
    # компания пользователя; null — admin (видит все)
    company_id: str | None = None
    is_admin: bool
    # false — вход выключен (AUTH_ENABLED=false), все запросы от demo
    auth_enabled: bool


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    # срок жизни токена, секунды
    expires_in: int
    user: UserInfo
