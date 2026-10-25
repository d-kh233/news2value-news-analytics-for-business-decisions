from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.auth import User, current_user
from app.schemas.common import ErrorResponse
from app.schemas.qa import FeedbackStats
from app.services import feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get(
    "/stats",
    response_model=FeedbackStats,
    responses={403: {"model": ErrorResponse, "description": "Только для администратора"}},
)
def stats(user: Annotated[User, Depends(current_user)]) -> FeedbackStats:
    """Метрики качества по оценкам (только admin): всего, доля useful, причины, типы инсайтов."""
    return feedback_service.stats(user)
