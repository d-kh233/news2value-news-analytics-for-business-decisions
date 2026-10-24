from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.schemas.common import ErrorResponse, Page
from app.schemas.company import Company, CompanyCreate, CompanyProfile, CompanyProfileUpdate
from app.schemas.digest import Digest, DigestPeriod
from app.schemas.insight import Insight, InsightType, Role
from app.schemas.job import JobAccepted
from app.services import company_service, job_service, mock_store

router = APIRouter(prefix="/companies", tags=["companies"])

NOT_FOUND = {404: {"model": ErrorResponse}}
CONFLICT = {409: {"model": ErrorResponse}}

Limit = Annotated[int, Query(ge=1, le=100, description="Сколько записей вернуть")]
Offset = Annotated[int, Query(ge=0, description="Сколько записей пропустить")]


@router.get("", response_model=Page[Company])
def list_companies(
    limit: Limit = 20,
    offset: Offset = 0,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Поиск по названию, id или отрасли (без учёта регистра)"),
    ] = None,
) -> Page[Company]:
    items, total = company_service.list_companies(limit, offset, q)
    return Page[Company](items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=Company, status_code=201)
def create_company(data: CompanyCreate) -> Company:
    return company_service.create_company(data)


@router.get("/{company_id}", response_model=Company, responses=NOT_FOUND)
def get_company(company_id: str) -> Company:
    return company_service.get_company(company_id)


@router.delete("/{company_id}", status_code=204, responses={**NOT_FOUND, **CONFLICT})
def delete_company(company_id: str) -> Response:
    """Удаляет только компанию, созданную через POST (source=manual); банковскую — 409."""
    company_service.delete_company(company_id)
    return Response(status_code=204)


@router.get("/{company_id}/profile", response_model=CompanyProfile, responses=NOT_FOUND)
def get_profile(company_id: str) -> CompanyProfile:
    """Профиль: данные банка + ручные поля (ручные главнее), источник поля — в field_sources."""
    return company_service.get_profile(company_id)


@router.put("/{company_id}/profile", response_model=CompanyProfile, responses=NOT_FOUND)
def put_profile(company_id: str, profile: CompanyProfileUpdate) -> CompanyProfile:
    """Поля из тела сохраняются как ручные; остальные ручные поля остаются как были."""
    return company_service.put_profile(company_id, profile)


@router.get("/{company_id}/insights", response_model=Page[Insight], responses=NOT_FOUND)
def list_insights(
    company_id: str,
    insight_type: Annotated[InsightType | None, Query(alias="type")] = None,
    min_importance: Annotated[int | None, Query(ge=0, le=100)] = None,
    date_from: Annotated[date | None, Query(description="YYYY-MM-DD, включительно")] = None,
    date_to: Annotated[date | None, Query(description="YYYY-MM-DD, включительно")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[Insight]:
    items, total = mock_store.list_insights(
        company_id,
        insight_type=insight_type,
        min_importance=min_importance,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return Page[Insight](items=items, total=total, limit=limit, offset=offset)


@router.get("/{company_id}/digest", response_model=Digest, responses=NOT_FOUND)
def get_digest(company_id: str, period: DigestPeriod) -> Digest:
    return mock_store.get_digest(company_id, period)


@router.post(
    "/{company_id}/insights/refresh",
    response_model=JobAccepted,
    status_code=202,
    responses=NOT_FOUND,
)
def refresh_insights(
    company_id: str,
    role: Annotated[Role | None, Query(description="Роль читателя; по умолчанию ceo")] = None,
) -> JobAccepted:
    """Поставить задачу агенту: новые инсайты компании заменят старые (выполняет worker).

    Если у компании уже есть задача pending/running, возвращается её job_id.
    """
    return JobAccepted(job_id=job_service.request_refresh(company_id, role))
