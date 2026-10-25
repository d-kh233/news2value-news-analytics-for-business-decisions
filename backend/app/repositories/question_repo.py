"""Вопросы по инсайтам (таблица insight_questions)."""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import QuestionRow


def lock_insight(session: Session, insight_id: str) -> None:
    """Замок на инсайт до конца транзакции: два ask подряд не превысят лимит вопросов."""
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(insight_id))))


def count_for_insight(session: Session, insight_id: str) -> int:
    stmt = select(func.count()).where(QuestionRow.insight_id == insight_id)
    return session.scalar(stmt) or 0


def add(session: Session, values: dict[str, Any]) -> None:
    session.add(QuestionRow(**values))
    session.flush()


def get(session: Session, question_id: str) -> QuestionRow | None:
    return session.get(QuestionRow, question_id)


def by_job(session: Session, job_id: str) -> QuestionRow | None:
    return session.scalars(select(QuestionRow).where(QuestionRow.job_id == job_id)).first()


def list_for_user(
    session: Session, insight_id: str, user_id: str, limit: int, offset: int
) -> tuple[list[QuestionRow], int]:
    """Вопросы пользователя по инсайту, старые первыми, и их общее число."""
    stmt = select(QuestionRow).where(
        QuestionRow.insight_id == insight_id, QuestionRow.user_id == user_id
    )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = stmt.order_by(QuestionRow.created_at, QuestionRow.id).limit(limit).offset(offset)
    return list(session.scalars(page)), total


def answered_before(session: Session, question: QuestionRow) -> list[QuestionRow]:
    """История для модели: ответы на прошлые вопросы того же пользователя по инсайту."""
    stmt = (
        select(QuestionRow)
        .where(
            QuestionRow.insight_id == question.insight_id,
            QuestionRow.user_id == question.user_id,
            QuestionRow.status == "done",
            QuestionRow.created_at <= question.created_at,
            QuestionRow.id != question.id,
        )
        .order_by(QuestionRow.created_at, QuestionRow.id)
    )
    return list(session.scalars(stmt))


def set_status(session: Session, question_id: str, status: str) -> None:
    session.execute(update(QuestionRow).where(QuestionRow.id == question_id).values(status=status))


def fail_for_jobs(session: Session, job_ids: list[str]) -> None:
    """Вопросы зависших задач ask (worker упал) — в failed вместе с задачей."""
    if job_ids:
        session.execute(
            update(QuestionRow)
            .where(QuestionRow.job_id.in_(job_ids), QuestionRow.status.in_(("pending", "running")))
            .values(status="failed")
        )


def set_answer(
    session: Session, question_id: str, answer: str, sources: list[dict[str, Any]]
) -> None:
    session.execute(
        update(QuestionRow)
        .where(QuestionRow.id == question_id)
        .values(answer=answer, sources=sources, status="done")
    )
