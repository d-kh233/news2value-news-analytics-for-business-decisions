from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Response

from app.schemas.common import ErrorResponse, Page
from app.schemas.company import Company, CompanyCreate, CompanyProfile
from app.schemas.digest import Digest, DigestPeriod
from app.schemas.insight import Insight, InsightType, Role
from app.schemas.job import JobAccepted
from app.services import mock_store

router = APIRouter(prefix="/companies", tags=["companies"])

NOT_FOUND = {404: {"model": ErrorResponse}}

Limit = Annotated[int, Query(ge=1, le=100, description="Сколько записей вернуть")]
Offset = Annotated[int, Query(ge=0, description="Сколько записей пропустить")]


@router.get("", response_model=Page[Company])
def list_companies(limit: Limit = 20, offset: Offset = 0) -> Page[Company]:
    items, total = mock_store.list_companies(limit, offset)
    return Page[Company](items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=Company, status_code=201)
def create_company(data: CompanyCreate) -> Company:
    return mock_store.create_company(data)


@router.get("/{company_id}", response_model=Company, responses=NOT_FOUND)
def get_company(company_id: str) -> Company:
    return mock_store.get_company(company_id)


@router.delete("/{company_id}", status_code=204, responses=NOT_FOUND)
def delete_company(company_id: str) -> Response:
    mock_store.delete_company(company_id)
    return Response(status_code=204)


@router.get("/{company_id}/profile", response_model=CompanyProfile, responses=NOT_FOUND)
def get_profile(company_id: str) -> CompanyProfile:
    return mock_store.get_profile(company_id)


@router.put("/{company_id}/profile", response_model=CompanyProfile, responses=NOT_FOUND)
def put_profile(company_id: str, profile: CompanyProfile) -> CompanyProfile:
    return mock_store.put_profile(company_id, profile)


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
    background: BackgroundTasks,
    role: Annotated[Role | None, Query(description="Роль читателя; по умолчанию ceo")] = None,
) -> JobAccepted:
    """Запустить агента: новые инсайты компании заменяют старые."""
    job_id = mock_store.start_refresh(company_id, role)
    background.add_task(mock_store.run_refresh, job_id)
    return JobAccepted(job_id=job_id)
