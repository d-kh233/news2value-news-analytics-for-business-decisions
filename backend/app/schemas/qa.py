"""Схемы ask и feedback (модуль B7b): добавлены сверх списка лида."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime
from app.schemas.insight import Source

FeedbackRating = Literal["useful", "not_useful"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1)


class AskResult(BaseModel):
    answer: str
    sources: list[Source]


class FeedbackRequest(BaseModel):
    rating: FeedbackRating
    comment: str | None = Field(default=None, max_length=500)


class Feedback(BaseModel):
    insight_id: str
    rating: FeedbackRating
    comment: str | None = None
    created_at: UtcDatetime
