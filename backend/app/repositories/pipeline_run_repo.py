"""Журнал запусков refresh (таблица pipeline_runs)."""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import PipelineRun


def create(session: Session, company_id: str) -> int:
    """Новый запуск со статусом running; вернуть его id."""
    run = PipelineRun(company_id=company_id, status="running")
    session.add(run)
    session.flush()
    return run.id


def finish(
    session: Session,
    run_id: int,
    status: str,
    steps: list[dict[str, Any]],
    error: str | None = None,
) -> None:
    """Записать итог запуска: статус, шаги из трейса агента, текст ошибки или пояснение."""
    session.execute(
        update(PipelineRun)
        .where(PipelineRun.id == run_id)
        .values(status=status, steps=steps, error=error, finished_at=func.now())
    )


def get(session: Session, run_id: int) -> PipelineRun | None:
    return session.get(PipelineRun, run_id)


def list_for_company(session: Session, company_id: str) -> list[PipelineRun]:
    """Запуски компании, сначала последние."""
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.company_id == company_id)
        .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
    )
    return list(session.scalars(stmt))
