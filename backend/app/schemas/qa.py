"""Схемы ask и feedback (модуль B7b): добавлены сверх списка лида."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import UtcDatetime
from app.schemas.insight import InsightType, Source
from app.schemas.job import JobAccepted, JobStatus

FeedbackRating = Literal["useful", "not_useful"]
# причина оценки not_useful: неточно, не про нас, непонятно, другое
FeedbackReason = Literal["inaccurate", "irrelevant", "unclear", "other"]
QUESTION_MAX_LEN = 500
COMMENT_MAX_LEN = 500
QUESTIONS_PER_INSIGHT = 20


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=QUESTION_MAX_LEN)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("вопрос пустой")
        return value


class AskAccepted(JobAccepted):
    """Ответ 202 на ask: задача и вопрос (его ответ появится в истории questions)."""

    question_id: str


class AskResult(BaseModel):
    answer: str
    sources: list[Source]


class Question(BaseModel):
    """Вопрос по инсайту и ответ (answer — null, пока задача не done)."""

    id: str
    insight_id: str
    question: str
    answer: str | None = None
    sources: list[Source] = Field(default_factory=list)
    status: JobStatus
    job_id: str | None = None
    created_at: UtcDatetime


class FeedbackRequest(BaseModel):
    rating: FeedbackRating
    comment: str | None = Field(default=None, max_length=COMMENT_MAX_LEN)
    # только для not_useful; при useful — 422
    reason: FeedbackReason | None = None

    @model_validator(mode="after")
    def _reason_only_for_not_useful(self) -> "FeedbackRequest":
        if self.reason is not None and self.rating == "useful":
            raise ValueError("reason указывается только при rating=not_useful")
        return self


class Feedback(BaseModel):
    insight_id: str
    rating: FeedbackRating
    comment: str | None = None
    created_at: UtcDatetime
    reason: FeedbackReason | None = None
    updated_at: UtcDatetime | None = None


class TypeStats(BaseModel):
    total: int
    useful_share: float | None = None


class FeedbackStats(BaseModel):
    """Метрики качества по оценкам: доля useful — 0..1 (null, если оценок нет)."""

    total: int
    useful: int
    not_useful: int
    useful_share: float | None = None
    # причины у оценок not_useful; без причины — ключ none
    by_reason: dict[str, int]
    by_type: dict[InsightType, TypeStats]
