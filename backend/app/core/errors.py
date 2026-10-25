import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ErrorResponse

logger = logging.getLogger(__name__)

_HTTP_CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}


class NotFoundError(Exception):
    """Объект не найден (превращается в 404)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ConflictError(Exception):
    """Действие противоречит состоянию объекта (превращается в 409)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ForbiddenError(Exception):
    """Нет прав на объект или действие (превращается в 403)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UnauthorizedError(Exception):
    """Пользователь не определён (превращается в 401)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _response(status: int, error: str, detail: str) -> JSONResponse:
    body = ErrorResponse(error=error, detail=detail)
    return JSONResponse(status_code=status, content=body.model_dump())


async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
    return _response(404, "not_found", exc.detail)


async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return _response(409, "conflict", exc.detail)


async def _forbidden(_: Request, exc: ForbiddenError) -> JSONResponse:
    return _response(403, "forbidden", exc.detail)


async def _unauthorized(_: Request, exc: UnauthorizedError) -> JSONResponse:
    return _response(401, "unauthorized", exc.detail)


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    return _response(exc.status_code, code, str(exc.detail))


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    parts = []
    for err in exc.errors():
        place = ".".join(str(p) for p in err["loc"][1:])
        parts.append(f"{place}: {err['msg']}" if place else err["msg"])
    return _response(422, "validation_error", "; ".join(parts))


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    # Трейсбек (список вызовов с местом сбоя) — только в лог, клиенту его не отдаём
    logger.error("Необработанная ошибка: %s %s", request.method, request.url.path, exc_info=exc)
    return _response(500, "internal_error", "Внутренняя ошибка сервера")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(NotFoundError, _not_found)
    app.add_exception_handler(ConflictError, _conflict)
    app.add_exception_handler(ForbiddenError, _forbidden)
    app.add_exception_handler(UnauthorizedError, _unauthorized)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected)
