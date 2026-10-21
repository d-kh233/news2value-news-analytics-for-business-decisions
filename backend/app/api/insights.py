from fastapi import APIRouter, BackgroundTasks, Response

from app.api.companies import NOT_FOUND
from app.schemas.insight import Insight
from app.schemas.job import JobAccepted
from app.schemas.qa import AskRequest, Feedback, FeedbackRequest
from app.services import mock_store

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/{insight_id}", response_model=Insight, responses=NOT_FOUND)
def get_insight(insight_id: str) -> Insight:
    return mock_store.get_insight(insight_id)


@router.post("/{insight_id}/ask", response_model=JobAccepted, status_code=202, responses=NOT_FOUND)
def ask(insight_id: str, body: AskRequest, background: BackgroundTasks) -> JobAccepted:
    job_id = mock_store.start_ask(insight_id, body.question)
    background.add_task(mock_store.run_ask, job_id)
    return JobAccepted(job_id=job_id)


@router.post(
    "/{insight_id}/feedback",
    response_model=Feedback,
    status_code=201,
    responses={**NOT_FOUND, 200: {"model": Feedback, "description": "Отзыв обновлён"}},
)
def feedback(insight_id: str, body: FeedbackRequest, response: Response) -> Feedback:
    saved, created = mock_store.save_feedback(insight_id, body)
    response.status_code = 201 if created else 200
    return saved
