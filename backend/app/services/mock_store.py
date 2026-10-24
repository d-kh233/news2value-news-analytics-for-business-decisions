"""Слой моковых данных для инсайтов, дайджеста, задач и отзывов (заглушка до B7).

Данные читаются из data/mocks/*.json и живут в памяти процесса. Изменения в файлы не пишутся.
Компании и профили для API живут в БД (company_service); моковые компании c-001…c-003
остались только как владельцы моковых инсайтов и как вход агента.
"""

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.errors import NotFoundError
from app.schemas.company import Company, CompanyProfile
from app.schemas.digest import Digest, DigestPeriod
from app.schemas.insight import Insight, InsightType
from app.schemas.job import Job
from app.schemas.qa import AskResult, Feedback, FeedbackRequest
from app.services import company_service

MOCKS_DIR = Path(__file__).resolve().parents[3] / "data" / "mocks"

logger = logging.getLogger(__name__)

_companies: dict[str, Company] = {}
_profiles: dict[str, CompanyProfile] = {}
_insights: dict[str, Insight] = {}
_feedback: dict[str, Feedback] = {}
_jobs: dict[str, Job] = {}
_job_kinds: dict[str, str] = {}  # job_id -> "refresh" | "ask"
_job_questions: dict[str, str] = {}
_job_insight: dict[str, str] = {}
_ask_results: dict[str, AskResult] = {}
_job_refresh: dict[str, tuple[str, str | None]] = {}  # job_id -> (company_id, role)
_job_runs: dict[str, dict[str, Any]] = {}  # job_id -> сводка прогона агента (план, трейс)


def _read(name: str) -> list[dict[str, Any]]:
    return json.loads((MOCKS_DIR / name).read_text(encoding="utf-8"))


def reset() -> None:
    """Загрузить исходные моки заново (используется при старте и в тестах)."""
    for store in (_companies, _profiles, _insights, _feedback, _jobs, _job_kinds):
        store.clear()
    _job_questions.clear()
    _job_insight.clear()
    _ask_results.clear()
    _job_refresh.clear()
    _job_runs.clear()
    for item in _read("companies.json"):
        _companies[item["id"]] = Company.model_validate(item)
    for item in _read("profiles.json"):
        _profiles[item["company_id"]] = CompanyProfile.model_validate(item)
    for item in _read("insights.json"):
        _insights[item["id"]] = Insight.model_validate(item)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _require_company(company_id: str) -> None:
    """Компания есть среди моковых (c-001…c-003) или в таблице companies, иначе 404.

    У компаний из БД моковых инсайтов нет: их лента пустая, это не ошибка.
    """
    if company_id in _companies or company_service.company_exists(company_id):
        return
    raise NotFoundError(f"Компания {company_id} не найдена")


def _require_insight(insight_id: str) -> Insight:
    insight = _insights.get(insight_id)
    if insight is None:
        raise NotFoundError(f"Инсайт {insight_id} не найден")
    return insight


def _mock_profile(company_id: str) -> CompanyProfile:
    """Профиль моковой компании для агента (эндпоинты профиля работают с БД)."""
    profile = _profiles.get(company_id)
    if profile is not None:
        return profile
    company = _companies[company_id]
    return CompanyProfile(
        company_id=company_id, industry=company.industry, regions=[company.region]
    )


# ---- инсайты ----


def list_insights(
    company_id: str,
    *,
    insight_type: InsightType | None,
    min_importance: int | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> tuple[list[Insight], int]:
    _require_company(company_id)
    items = [i for i in _insights.values() if i.company_id == company_id]
    if insight_type is not None:
        items = [i for i in items if i.type == insight_type]
    if min_importance is not None:
        items = [i for i in items if i.importance >= min_importance]
    if date_from is not None:
        items = [i for i in items if i.created_at.astimezone(UTC).date() >= date_from]
    if date_to is not None:
        items = [i for i in items if i.created_at.astimezone(UTC).date() <= date_to]
    items.sort(key=lambda i: i.created_at, reverse=True)
    return items[offset : offset + limit], len(items)


def get_insight(insight_id: str) -> Insight:
    return _require_insight(insight_id)


def get_digest(company_id: str, period: DigestPeriod) -> Digest:
    _require_company(company_id)
    items = [i for i in _insights.values() if i.company_id == company_id]
    if not items:
        return Digest(
            company_id=company_id, period=period, summary="Новых инсайтов нет.", insight_ids=[]
        )
    # «Сегодня» для моков — время самого свежего инсайта, чтобы дайджест не пустел со временем
    now = max(i.created_at for i in items)
    window = timedelta(days=1 if period == "day" else 7)
    chosen = sorted(
        (i for i in items if i.created_at > now - window),
        key=lambda i: i.importance,
        reverse=True,
    )
    risks = sum(1 for i in chosen if i.type == "risk")
    opportunities = sum(1 for i in chosen if i.type == "opportunity")
    label = "сутки" if period == "day" else "неделю"
    summary = (
        f"За {label}: {len(chosen)} инсайт(ов), из них рисков — {risks}, "
        f"возможностей — {opportunities}. Самое важное: {chosen[0].title}."
    )
    return Digest(
        company_id=company_id, period=period, summary=summary, insight_ids=[i.id for i in chosen]
    )


# ---- задачи ----


def _create_job(kind: str) -> str:
    job = Job(id=_new_id("job"), status="pending")
    _jobs[job.id] = job
    _job_kinds[job.id] = kind
    return job.id


def _set_status(job_id: str, status: str, error: str | None = None) -> None:
    _jobs[job_id] = Job(id=job_id, status=status, error=error)  # type: ignore[arg-type]


def start_refresh(company_id: str, role: str | None = None) -> str:
    _require_company(company_id)
    job_id = _create_job("refresh")
    _job_refresh[job_id] = (company_id, role)
    return job_id


def run_refresh(job_id: str) -> None:
    """Фоновая генерация инсайтов агентом (agent/). Старые инсайты компании заменяются новыми.

    Если агент упал, задача получает статус failed, а прежние инсайты остаются.
    """
    _set_status(job_id, "running")
    company_id, role = _job_refresh[job_id]
    company = _companies.get(company_id)
    if company is None and not company_service.company_exists(company_id):
        _set_status(job_id, "failed", "Компания была удалена")
        return
    try:
        from app.ml_ports import agent_port

        if company is not None:
            cards, run = agent_port.generate_insights(
                company_id,
                role,
                company=company.model_dump(),
                profile=_mock_profile(company_id).model_dump(),
            )
        else:
            # компания из БД: агент сам читает её строку из таблицы companies
            cards, run = agent_port.generate_insights(company_id, role)
        fresh = [Insight.model_validate(card) for card in cards]
    except Exception as exc:  # noqa: BLE001 — любой сбой агента превращаем в статус задачи
        logger.exception("Агент не смог обновить инсайты %s", company_id)
        _set_status(job_id, "failed", f"{type(exc).__name__}: {exc}"[:500])
        return
    for insight_id in [i.id for i in _insights.values() if i.company_id == company_id]:
        del _insights[insight_id]
        _feedback.pop(insight_id, None)
    for insight in fresh:
        _insights[insight.id] = insight
    _job_runs[job_id] = run
    _set_status(job_id, "done")


def get_job_run(job_id: str) -> dict[str, Any]:
    """Сводка прогона агента по refresh-задаче (план и трейс — для панели трассировки)."""
    get_job(job_id)
    if job_id not in _job_runs:
        raise NotFoundError(f"У задачи {job_id} нет прогона агента")
    return _job_runs[job_id]


def start_ask(insight_id: str, question: str) -> str:
    _require_insight(insight_id)
    job_id = _create_job("ask")
    _job_questions[job_id] = question
    _ask_results[job_id] = AskResult(answer="", sources=[])
    _job_insight[job_id] = insight_id
    return job_id


def run_ask(job_id: str) -> None:
    """Имитация ответа на вопрос по инсайту."""
    _set_status(job_id, "running")
    insight = _insights.get(_job_insight[job_id])
    if insight is None:
        _set_status(job_id, "failed", "Инсайт был удалён")
        return
    answer = (
        f"По теме «{insight.title}»: {insight.recommended_action} "
        f"Вопрос был: «{_job_questions[job_id]}». Это демонстрационный ответ."
    )
    _ask_results[job_id] = AskResult(answer=answer, sources=insight.sources)
    _set_status(job_id, "done")


def get_job(job_id: str) -> Job:
    job = _jobs.get(job_id)
    if job is None:
        raise NotFoundError(f"Задача {job_id} не найдена")
    return job


def get_job_result(job_id: str) -> AskResult:
    job = get_job(job_id)
    if _job_kinds.get(job_id) != "ask":
        raise NotFoundError(f"У задачи {job_id} нет результата")
    if job.status != "done":
        raise NotFoundError(f"Результат задачи {job_id} ещё не готов")
    return _ask_results[job_id]


# ---- отзывы ----


def save_feedback(insight_id: str, data: FeedbackRequest) -> tuple[Feedback, bool]:
    """Сохранить отзыв; второй элемент — True, если отзыв на инсайт первый."""
    _require_insight(insight_id)
    created = insight_id not in _feedback
    feedback = Feedback(
        insight_id=insight_id,
        rating=data.rating,
        comment=data.comment,
        created_at=datetime.now(UTC).replace(microsecond=0),
    )
    _feedback[insight_id] = feedback
    return feedback, created


reset()
