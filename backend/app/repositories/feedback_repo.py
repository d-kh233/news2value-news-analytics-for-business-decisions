"""Оценки инсайтов (таблица insight_feedback): одна на пару (инсайт, пользователь)."""

from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import FeedbackRow


def upsert(session: Session, values: dict[str, Any]) -> tuple[FeedbackRow, bool]:
    """Сохранить оценку; повторная от того же пользователя обновляет прежнюю.

    Второй элемент — True, если строка создана (xmax = 0 у только что вставленной строки).
    """
    stmt = insert(FeedbackRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_insight_feedback_user",
        set_={
            "rating": stmt.excluded.rating,
            "reason": stmt.excluded.reason,
            "comment": stmt.excluded.comment,
            "insight_type": stmt.excluded.insight_type,
            "updated_at": func.now(),
        },
    ).returning(FeedbackRow.id, literal_column("xmax = 0").label("created"))
    row_id, created = session.execute(stmt).one()
    session.flush()
    row = session.get(FeedbackRow, row_id)
    session.refresh(row)
    return row, bool(created)


def get(session: Session, insight_id: str, user_id: str) -> FeedbackRow | None:
    stmt = select(FeedbackRow).where(
        FeedbackRow.insight_id == insight_id, FeedbackRow.user_id == user_id
    )
    return session.scalars(stmt).first()


def counts(session: Session) -> list[dict[str, Any]]:
    """Число оценок по (тип инсайта, оценка, причина) — из этого считается статистика."""
    stmt = select(
        FeedbackRow.insight_type,
        FeedbackRow.rating,
        FeedbackRow.reason,
        func.count().label("n"),
    ).group_by(FeedbackRow.insight_type, FeedbackRow.rating, FeedbackRow.reason)
    return [dict(r) for r in session.execute(stmt).mappings()]
