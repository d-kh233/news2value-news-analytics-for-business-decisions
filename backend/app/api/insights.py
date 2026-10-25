from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Response

from app.api.companies import NOT_FOUND, Limit, Offset
from app.core.auth import User, current_user
from app.schemas.common import ErrorResponse, Page
from app.schemas.insight import Insight
from app.schemas.qa import AskAccepted, AskRequest, Feedback, FeedbackRequest, Question
from app.services import feedback_service, insight_service, qa_service

router = APIRouter(prefix="/insights", tags=["insights"])

FORBIDDEN = {403: {"model": ErrorResponse, "description": "Нет доступа к инсайту"}}
CONFLICT = {409: {"model": ErrorResponse, "description": "Лимит 20 вопросов к инсайту"}}
CurrentUser = Annotated[User, Depends(current_user)]


@router.get("/{insight_id}", response_model=Insight, responses=NOT_FOUND)
def get_insight(insight_id: str) -> Insight:
    """Полная карточка; источники-новости — с данными из собранных документов."""
    return insight_service.get_insight(insight_id)


@router.post(
    "/{insight_id}/ask",
    response_model=AskAccepted,
    status_code=202,
    responses={**NOT_FOUND, **FORBIDDEN, **CONFLICT},
)
def ask(
    insight_id: str, body: AskRequest, background: BackgroundTasks, user: CurrentUser
) -> AskAccepted:
    """Вопрос по инсайту (до 500 символов, не больше 20 на инсайт). Ответ готовит фоновая
    задача: статус — GET /jobs/{job_id}, ответ — GET /jobs/{job_id}/result и история questions."""
    accepted, work = qa_service.ask(insight_id, body.question, user)
    if work is not None:
        background.add_task(work)
    return accepted


@router.get(
    "/{insight_id}/questions",
    response_model=Page[Question],
    responses={**NOT_FOUND, **FORBIDDEN},
)
def list_questions(
    insight_id: str, user: CurrentUser, limit: Limit = 20, offset: Offset = 0
) -> Page[Question]:
    """История вопросов пользователя по инсайту и ответов на них, старые первыми."""
    return qa_service.list_questions(insight_id, user, limit, offset)


@router.post(
    "/{insight_id}/feedback",
    response_model=Feedback,
    status_code=201,
    responses={
        **NOT_FOUND,
        **FORBIDDEN,
        200: {"model": Feedback, "description": "Отзыв обновлён"},
    },
)
def feedback(
    insight_id: str, body: FeedbackRequest, response: Response, user: CurrentUser
) -> Feedback:
    """Оценка инсайта: одна на пользователя, повторная обновляет прежнюю (200 вместо 201)."""
    saved, created = feedback_service.save(insight_id, body, user)
    response.status_code = 201 if created else 200
    return saved


@router.get(
    "/{insight_id}/feedback",
    response_model=Feedback | None,
    responses={**NOT_FOUND, **FORBIDDEN},
)
def my_feedback(insight_id: str, user: CurrentUser) -> Feedback | None:
    """Оценка текущего пользователя (подсветить кнопку); null — ещё не оценивал."""
    return feedback_service.get_mine(insight_id, user)
