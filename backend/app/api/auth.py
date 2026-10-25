from fastapi import APIRouter

from app.core.auth import CurrentUser
from app.schemas.auth import LoginRequest, LoginResponse, UserInfo
from app.schemas.common import ErrorResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Неверный email или пароль"},
        503: {"model": ErrorResponse, "description": "На сервере не задан JWT_SECRET"},
    },
)
def login(body: LoginRequest) -> LoginResponse:
    """Вход: токен для заголовка Authorization: Bearer <access_token>.

    Работает и при AUTH_ENABLED=false (токен тогда просто не проверяется)."""
    return auth_service.login(body.email, body.password)


@router.get("/me", response_model=UserInfo, responses={401: {"model": ErrorResponse}})
def me(user: CurrentUser) -> UserInfo:
    """Кто я: роль и компания. При AUTH_ENABLED=false — пользователь demo (admin)."""
    return auth_service.user_info(user)
