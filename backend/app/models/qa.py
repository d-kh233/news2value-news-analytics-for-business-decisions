"""Вопросы по инсайтам и оценки инсайтов (модуль B7b, миграция 0006).

Внешнего ключа на insights нет: refresh пересоздаёт инсайты компании, а история вопросов и
оценки должны его пережить. Поэтому у строк есть копии company_id (и insight_type у оценки —
для статистики по типам после refresh).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

QUESTION_STATUSES = ("pending", "running", "done", "failed")
RATINGS = ("useful", "not_useful")
REASONS = ("inaccurate", "irrelevant", "unclear", "other")
QUESTION_MAX_LEN = 500
COMMENT_MAX_LEN = 500


class QuestionRow(Base):
    """Вопрос пользователя по инсайту и ответ; статус повторяет задачу ask (jobs)."""

    __tablename__ = "insight_questions"
    __table_args__ = (
        CheckConstraint(f"status IN {QUESTION_STATUSES}", name="ck_insight_questions_status"),
        CheckConstraint(
            f"char_length(question) <= {QUESTION_MAX_LEN}", name="ck_insight_questions_len"
        ),
        Index("ix_insight_questions_insight", "insight_id", "created_at"),
    )

    # q-<8 hex>
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    insight_id: Mapped[str] = mapped_column(Text)
    company_id: Mapped[str] = mapped_column(
        Text, ForeignKey("companies.synthetic_u", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    # источники ответа — в схеме Source (title, url, published_at, source_kind, id)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    job_id: Mapped[str | None] = mapped_column(Text, ForeignKey("jobs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedbackRow(Base):
    """Оценка инсайта пользователем: одна на пару (инсайт, пользователь), повторная обновляет."""

    __tablename__ = "insight_feedback"
    __table_args__ = (
        UniqueConstraint("insight_id", "user_id", name="uq_insight_feedback_user"),
        CheckConstraint(f"rating IN {RATINGS}", name="ck_insight_feedback_rating"),
        CheckConstraint(
            f"reason IS NULL OR reason IN {REASONS}", name="ck_insight_feedback_reason"
        ),
        CheckConstraint(
            f"comment IS NULL OR char_length(comment) <= {COMMENT_MAX_LEN}",
            name="ck_insight_feedback_comment",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    insight_id: Mapped[str] = mapped_column(Text)
    company_id: Mapped[str] = mapped_column(
        Text, ForeignKey("companies.synthetic_u", ondelete="CASCADE")
    )
    insight_type: Mapped[str] = mapped_column(String(12))
    user_id: Mapped[str] = mapped_column(Text)
    rating: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str | None] = mapped_column(String(12))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
