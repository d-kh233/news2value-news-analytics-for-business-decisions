"""Трассировка инсайта (GET /insights/{id}/trace): каким запуском, из каких источников, по каким
признакам профиля и какой версией промптов он сделан."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.common import UtcDatetime
from app.schemas.insight import SourceKind


class TraceStep(BaseModel):
    """Шаг запуска — как его записал генератор (TraceStep агента или шаг заглушки).

    Обязательных полей нет: шаги приходят от ML-части как есть, лишние поля сохраняются.
    duration_ms — время шага в миллисекундах.
    """

    model_config = ConfigDict(extra="allow")

    step: int | None = None
    name: str | None = None
    tool: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    note: str | None = None
    input: Any = None
    output: Any = None


class TraceSource(BaseModel):
    """Источник инсайта. document_id и source_name — если это собранный документ (documents)."""

    id: str | None = None
    document_id: int | None = None
    title: str
    url: str
    published_at: UtcDatetime | None = None
    source_kind: SourceKind
    source_name: str | None = None


class InsightTrace(BaseModel):
    insight_id: str
    company_id: str
    # id запуска pipeline_runs (null — инсайт из моков или сделан до миграции 0007 без запуска)
    run_id: int | None = None
    # id прогона у генератора (агента или заглушки)
    generator_run_id: str | None = None
    run_status: Literal["running", "done", "empty", "failed"] | None = None
    role: str | None = None
    llm_mode: str | None = None
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    duration_ms: int | None = None
    steps: list[TraceStep]
    sources: list[TraceSource]
    # null — ML это поле не отдал
    used_profile_fields: list[str] | None = None
    prompt_version: str | None = None
    # кто сделал: llm — модель, rules — шаблоны агента, fake — заглушка ML_MODE=fake
    generated_by: Literal["llm", "rules", "fake"] | None = None
