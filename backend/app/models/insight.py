"""Инсайты компаний и журнал запусков refresh (модуль B5)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

INSIGHT_TYPES = ("risk", "opportunity", "event")
# fake — заглушка ML_MODE=fake; в payload у неё generated_by = rules (схема Insight не меняется)
GENERATORS = ("llm", "rules", "fake")
RUN_STATUSES = ("running", "done", "empty", "failed")


class InsightRow(Base):
    """Карточка инсайта: payload — ровно то, что отдаёт API (схема app/schemas/insight.py Insight,
    включая details с 6 блоками llm.schemas.Insight и used_profile_fields).

    Остальные колонки — копии полей payload для фильтров и проверок без разбора JSON.
    document_ids — id строк documents, на которые ссылается карточка (N:doc-<id>).
    Внешний ключ на массив не поставить: id проверяются при сохранении (services/refresh.py).
    """

    __tablename__ = "insights"
    __table_args__ = (
        CheckConstraint(f"type IN {INSIGHT_TYPES}", name="ck_insights_type"),
        CheckConstraint(f"generated_by IN {GENERATORS}", name="ck_insights_generated_by"),
        CheckConstraint("importance BETWEEN 0 AND 100", name="ck_insights_importance"),
    )

    # id от агента: <компания>-<новость>-<роль>
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[str] = mapped_column(
        Text, ForeignKey("companies.synthetic_u", ondelete="CASCADE"), index=True
    )
    document_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), server_default=sql_text("'{}'::bigint[]")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    type: Mapped[str] = mapped_column(String(12))
    importance: Mapped[int] = mapped_column(Integer)
    role: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(10))
    prompt_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    """Один запуск refresh для компании.

    status: running → done (инсайты заменены) | empty (агент не вернул карточек, старые остались)
    | failed (ошибка, старые остались). steps — трейс агента как есть, бэкенд шаги не считает;
    summary — остальная сводка прогона (для GET /jobs/{id}/run).
    """

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_pipeline_runs_status"),
        Index("ix_pipeline_runs_company_started", "company_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(
        Text, ForeignKey("companies.synthetic_u", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(10), server_default="running")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    # сводка генератора без трейса: run_id агента, role, llm_mode, plan, llm_usage (B6)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
