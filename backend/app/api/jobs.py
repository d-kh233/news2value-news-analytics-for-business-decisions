from typing import Any

from fastapi import APIRouter

from app.api.companies import AUTH, NOT_FOUND
from app.core.auth import CurrentUser
from app.schemas.job import Job
from app.schemas.qa import AskResult
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=Job, responses={**NOT_FOUND, **AUTH})
def get_job(job_id: str, user: CurrentUser) -> Job:
    return job_service.get_job(job_id, user)


@router.get("/{job_id}/result", response_model=AskResult, responses={**NOT_FOUND, **AUTH})
def get_job_result(job_id: str, user: CurrentUser) -> AskResult:
    """Ответ задачи ask, когда она done. Для refresh-задач и неготовых — 404."""
    return job_service.get_job_result(job_id, user)


@router.get("/{job_id}/run", response_model=dict[str, Any], responses={**NOT_FOUND, **AUTH})
def get_job_run(job_id: str, user: CurrentUser) -> dict[str, Any]:
    """План и трейс прогона агента по refresh-задаче (для панели трассировки).

    404, если задачи нет, она ещё не завершена или это не refresh.
    """
    return job_service.get_job_run(job_id, user)
