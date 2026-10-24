"""Фоновые задачи refresh (B6): постановка из API, выполнение worker'ом, статус для API.

API только кладёт задачу в таблицу jobs и сразу отвечает 202. Конвейер (services/refresh.py)
выполняет отдельный процесс worker (app/worker.py). У компании не больше одной активной
(pending/running) задачи: повторный refresh отдаёт её job_id, роль при этом не важна —
refresh всё равно заменяет все инсайты компании.

Итог задачи: done — конвейер отработал (в том числе 0 карточек, status=empty у pipeline_runs:
пояснение там); failed — ошибка, текст в error. running дольше JOB_TIMEOUT_MINUTES — worker,
вероятно, упал: задача и её запуск pipeline_runs получают failed (fail_stale).

Задачи ask пока живут в mock_store (до B7): GET /jobs/{id} ищет сначала там, потом в БД.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import NotFoundError
from app.repositories import job_repo, pipeline_run_repo
from app.schemas.job import Job
from app.services import company_service, refresh

logger = logging.getLogger(__name__)

KIND_REFRESH = "refresh"
# у POST .../refresh нет параметра period: окно агента — неделя, как в run_pipeline.py
DEFAULT_PERIOD = "week"
INSERT_ATTEMPTS = 3


@dataclass
class ClaimedJob:
    """Задача, которую worker взял в работу (уже running)."""

    id: str
    company_id: str
    kind: str
    params: dict[str, Any]


def _new_id() -> str:
    return f"job-{uuid.uuid4().hex[:8]}"


def request_refresh(company_id: str, role: str | None) -> str:
    """Поставить refresh компании; если активная задача уже есть — вернуть её id."""
    if not company_service.company_exists(company_id):
        raise NotFoundError(f"Компания {company_id} не найдена")
    params = {"role": role, "period": DEFAULT_PERIOD}
    for _ in range(INSERT_ATTEMPTS):
        with get_session() as session:
            job_id = _new_id()
            if job_repo.insert_pending(session, job_id, company_id, KIND_REFRESH, params):
                session.commit()
                logger.info("refresh %s: задача %s поставлена (%s)", company_id, job_id, params)
                return job_id
            active = job_repo.active_id(session, company_id, KIND_REFRESH)
        if active is not None:
            logger.info("refresh %s: уже есть активная задача %s", company_id, active)
            return active
        # активная задача завершилась между вставкой и чтением — пробуем вставить снова
    raise RuntimeError(f"Не удалось поставить refresh компании {company_id}")


def get_job(job_id: str) -> Job:
    # mock_store при импорте читает data/mocks — берём его здесь, чтобы worker без моков работал
    from app.services import mock_store

    try:
        return mock_store.get_job(job_id)  # задачи ask (моки до B7)
    except NotFoundError:
        pass
    with get_session() as session:
        row = job_repo.get(session, job_id)
        if row is None:
            raise NotFoundError(f"Задача {job_id} не найдена")
        return Job(id=row.id, status=row.status, error=row.error)  # type: ignore[arg-type]


def get_job_run(job_id: str) -> dict[str, Any]:
    """План и трейс прогона по завершённой (done) задаче refresh — для панели трассировки."""
    with get_session() as session:
        job = job_repo.get(session, job_id)
        if job is None:
            raise NotFoundError(f"Задача {job_id} не найдена")
        run = pipeline_run_repo.get(session, job.run_id) if job.run_id is not None else None
        if job.status != "done" or run is None:
            raise NotFoundError(f"У задачи {job_id} нет завершённого прогона")
        summary = run.summary or {}
        return {
            "run_id": summary.get("run_id"),
            "role": summary.get("role"),
            "llm_mode": summary.get("llm_mode"),
            "plan": summary.get("plan") or [],
            "trace": run.steps,
            "llm_usage": summary.get("llm_usage") or {},
        }


# ---------- для worker ----------


def claim_next() -> ClaimedJob | None:
    """Взять старейшую pending (она становится running) или None, если задач нет."""
    with get_session() as session:
        row = job_repo.claim_next(session)
        if row is None:
            return None
        job = ClaimedJob(row.id, row.company_id, row.kind, dict(row.params or {}))
        session.commit()
    return job


def _save_run_id(job_id: str, run_id: int) -> None:
    with get_session() as session:
        job_repo.set_run_id(session, job_id, run_id)
        session.commit()


def execute(job: ClaimedJob) -> str:
    """Выполнить задачу и записать итог; вернуть итоговый статус (done | failed)."""
    try:
        result = refresh.run_for_company(
            job.company_id,
            job.params.get("role"),
            job.params.get("period", DEFAULT_PERIOD),
            on_start=lambda run_id: _save_run_id(job.id, run_id),
        )
        status, error = ("failed", result.error) if result.status == "failed" else ("done", None)
    except NotFoundError as exc:  # компанию удалили, пока задача ждала
        status, error = "failed", exc.detail
    except Exception as exc:  # noqa: BLE001 — любой сбой превращаем в статус задачи
        logger.exception("задача %s: сбой конвейера", job.id)
        status, error = "failed", f"{type(exc).__name__}: {exc}"[: refresh.ERROR_MAX_LEN]
    with get_session() as session:
        written = job_repo.finish(session, job.id, status, error)
        session.commit()
    if not written:
        logger.warning("задача %s уже не running — итог %s не записан", job.id, status)
    return status


def fail_stale() -> list[str]:
    """running дольше JOB_TIMEOUT_MINUTES — в failed (вместе с запуском pipeline_runs)."""
    minutes = get_settings().job_timeout_minutes
    error = f"Задача выполнялась дольше {minutes:g} мин: рабочий процесс (worker), вероятно, упал"
    with get_session() as session:
        marked = job_repo.fail_stale(session, timedelta(minutes=minutes), error)
        run_ids = [run_id for _, run_id in marked if run_id is not None]
        pipeline_run_repo.fail_running(session, run_ids, error)
        session.commit()
    for job_id, _ in marked:
        logger.warning("задача %s: %s", job_id, error)
    return [job_id for job_id, _ in marked]
