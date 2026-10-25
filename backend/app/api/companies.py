from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response
from fastapi.exceptions import RequestValidationError

from app.core.auth import AdminUser, CompanyUser, CurrentUser
from app.ml_ports.agent_port import ROLES
from app.schemas.common import ErrorResponse, Page
from app.schemas.company import Company, CompanyCreate, CompanyProfile, CompanyProfileUpdate
from app.schemas.digest import Digest, DigestPeriod
from app.schemas.insight import InsightPage, InsightType, Role
from app.schemas.job import JobAccepted
from app.services import company_service, insight_service, job_service

router = APIRouter(prefix="/companies", tags=["companies"])

NOT_FOUND = {404: {"model": ErrorResponse}}
CONFLICT = {409: {"model": ErrorResponse}}
# ответы при AUTH_ENABLED=true: нет токена — 401, чужая компания или не admin — 403
AUTH = {
    401: {"model": ErrorResponse, "description": "Нужен вход (AUTH_ENABLED=true)"},
    403: {"model": ErrorResponse, "description": "Нет доступа к компании"},
}

Limit = Annotated[int, Query(ge=1, le=100, description="Сколько записей вернуть")]
Offset = Annotated[int, Query(ge=0, description="Сколько записей пропустить")]


@router.get("", response_model=Page[Company], responses=AUTH)
def list_companies(
    user: CurrentUser,
    limit: Limit = 20,
    offset: Offset = 0,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Поиск по названию, id или отрасли (без учёта регистра)"),
    ] = None,
) -> Page[Company]:
    """Компании: admin видит все, остальные — только свою."""
    items, total = company_service.list_companies(limit, offset, q, user.company_ids)
    return Page[Company](items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=Company, status_code=201, responses=AUTH)
def create_company(data: CompanyCreate, _: AdminUser) -> Company:
    """Создать компанию вручную (только admin)."""
    return company_service.create_company(data)


@router.get("/{company_id}", response_model=Company, responses={**NOT_FOUND, **AUTH})
def get_company(company_id: str, _: CompanyUser) -> Company:
    return company_service.get_company(company_id)


@router.delete("/{company_id}", status_code=204, responses={**NOT_FOUND, **CONFLICT, **AUTH})
def delete_company(company_id: str, _: AdminUser) -> Response:
    """Удаляет только компанию, созданную через POST (source=manual); банковскую — 409.
    Только admin."""
    company_service.delete_company(company_id)
    return Response(status_code=204)


@router.get("/{company_id}/profile", response_model=CompanyProfile, responses={**NOT_FOUND, **AUTH})
def get_profile(company_id: str, _: CompanyUser) -> CompanyProfile:
    """Профиль: данные банка + ручные поля (ручные главнее), источник поля — в field_sources."""
    return company_service.get_profile(company_id)


@router.put("/{company_id}/profile", response_model=CompanyProfile, responses={**NOT_FOUND, **AUTH})
def put_profile(company_id: str, profile: CompanyProfileUpdate, _: CompanyUser) -> CompanyProfile:
    """Поля из тела сохраняются как ручные; остальные ручные поля остаются как были."""
    return company_service.put_profile(company_id, profile)


@router.get("/{company_id}/insights", response_model=InsightPage, responses={**NOT_FOUND, **AUTH})
def list_insights(
    company_id: str,
    _: CompanyUser,
    insight_type: Annotated[InsightType | None, Query(alias="type")] = None,
    min_importance: Annotated[int | None, Query(ge=0, le=100)] = None,
    date_from: Annotated[date | None, Query(description="YYYY-MM-DD, включительно")] = None,
    date_to: Annotated[date | None, Query(description="YYYY-MM-DD, включительно")] = None,
    industry: Annotated[
        str | None,
        Query(
            max_length=50,
            description="Отрасль документа-источника: ОКВЭД (41 или okved:41) или тег (economy)",
        ),
    ] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> InsightPage:
    """Лента: сначала важные, при равной важности — свежие. Пустая лента — 200 с подсказкой hint;
    refresh_job_id — задача обновления, которая сейчас идёт."""
    if date_from and date_to and date_from > date_to:
        raise RequestValidationError(
            [{"loc": ("query", "date_from"), "msg": "date_from позже date_to", "type": "value"}]
        )
    return insight_service.list_insights(
        company_id,
        insight_type=insight_type,
        min_importance=min_importance,
        date_from=date_from,
        date_to=date_to,
        industry=industry,
        limit=limit,
        offset=offset,
    )


@router.get("/{company_id}/digest", response_model=Digest, responses={**NOT_FOUND, **AUTH})
def get_digest(company_id: str, period: DigestPeriod, _: CompanyUser) -> Digest:
    """Самые важные инсайты за сутки или неделю (до 10) и их краткая сводка."""
    return insight_service.get_digest(company_id, period)


@router.post(
    "/{company_id}/insights/refresh",
    response_model=JobAccepted,
    status_code=202,
    responses={**NOT_FOUND, **AUTH},
)
def refresh_insights(
    company_id: str,
    user: CompanyUser,
    role: Annotated[
        Role | None,
        Query(description="Роль читателя; по умолчанию роль пользователя, у admin — ceo"),
    ] = None,
) -> JobAccepted:
    """Поставить задачу агенту: новые инсайты компании заменят старые (выполняет worker).

    Если у компании уже есть задача pending/running, возвращается её job_id.
    """
    if role is None and user.role in ROLES:
        role = user.role  # type: ignore[assignment]
    return JobAccepted(job_id=job_service.request_refresh(company_id, role, user.id))
