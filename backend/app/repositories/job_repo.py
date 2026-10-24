"""Фоновые задачи (таблица jobs): постановка, захват worker'ом, итог, зависшие задачи."""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import JobRow
from app.models.job import ACTIVE_JOB_STATUSES

# условие частичного индекса uq_jobs_active_company — текстом, как в миграции: Postgres
# сопоставляет ON CONFLICT с индексом по условию, а с параметрами запроса этого не сделать
_ACTIVE = sql_text("status IN ('pending', 'running')")


def insert_pending(
    session: Session, job_id: str, company_id: str, kind: str, params: dict[str, Any]
) -> bool:
    """Новая задача pending; False — у компании уже есть активная задача этого вида."""
    stmt = (
        insert(JobRow)
        .values(id=job_id, company_id=company_id, kind=kind, params=params, status="pending")
        .on_conflict_do_nothing(index_elements=["company_id", "kind"], index_where=_ACTIVE)
        .returning(JobRow.id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def active_id(session: Session, company_id: str, kind: str) -> str | None:
    """id задачи компании в статусе pending или running."""
    stmt = select(JobRow.id).where(
        JobRow.company_id == company_id,
        JobRow.kind == kind,
        JobRow.status.in_(ACTIVE_JOB_STATUSES),
    )
    return session.scalars(stmt).first()


def get(session: Session, job_id: str) -> JobRow | None:
    return session.get(JobRow, job_id)


def claim_next(session: Session) -> JobRow | None:
    """Взять старейшую pending и перевести в running. Коммит — за вызывающим.

    FOR UPDATE SKIP LOCKED: строку, которую в этот момент берёт другой worker (она под его
    замком до коммита), пропускаем, а не ждём — два worker'а не возьмут одну задачу.
    """
    stmt = (
        select(JobRow)
        .where(JobRow.status == "pending")
        .order_by(JobRow.created_at, JobRow.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalars(stmt).first()
    if job is None:
        return None
    job.status = "running"
    job.started_at = func.now()
    session.flush()
    session.refresh(job)
    return job


def set_run_id(session: Session, job_id: str, run_id: int) -> None:
    session.execute(update(JobRow).where(JobRow.id == job_id).values(run_id=run_id))


def finish(session: Session, job_id: str, status: str, error: str | None = None) -> bool:
    """Итог задачи. False — задача уже не running (её пометили зависшей), итог не записан."""
    result = session.execute(
        update(JobRow)
        .where(JobRow.id == job_id, JobRow.status == "running")
        .values(status=status, error=error, finished_at=func.now())
    )
    return result.rowcount == 1


def fail_stale(session: Session, older_than: timedelta, error: str) -> list[tuple[str, int | None]]:
    """running дольше older_than — в failed; вернуть (id задачи, run_id) помеченных."""
    stmt = (
        update(JobRow)
        .where(JobRow.status == "running", JobRow.started_at < func.now() - older_than)
        .values(status="failed", error=error, finished_at=func.now())
        .returning(JobRow.id, JobRow.run_id)
    )
    return [(job_id, run_id) for job_id, run_id in session.execute(stmt)]
