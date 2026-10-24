"""Фоновые задачи (jobs) и кэш ответов генератора (llm_cache) — модуль B6."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

JOB_KINDS = ("refresh",)
JOB_STATUSES = ("pending", "running", "done", "failed")
# у компании одновременно не больше одной такой задачи каждого вида
ACTIVE_JOB_STATUSES = ("pending", "running")


def _sql_list(values: tuple[str, ...]) -> str:
    """('a', 'b') для SQL; у кортежа из одного элемента Python пишет ('a',) — так нельзя."""
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


class JobRow(Base):
    """Задача для worker: pending → running → done | failed.

    params — параметры запуска ({role, period} у refresh), run_id — запись pipeline_runs,
    которую создал конвейер (появляется, как только запуск начался).
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(f"kind IN {_sql_list(JOB_KINDS)}", name="ck_jobs_kind"),
        CheckConstraint(f"status IN {_sql_list(JOB_STATUSES)}", name="ck_jobs_status"),
        # частичный уникальный индекс: вторая активная задача той же компании не вставится
        Index(
            "uq_jobs_active_company",
            "company_id",
            "kind",
            unique=True,
            postgresql_where=sql_text(f"status IN {_sql_list(ACTIVE_JOB_STATUSES)}"),
        ),
        Index("ix_jobs_status_created", "status", "created_at"),
    )

    # job-<8 hex>, как в контракте
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[str] = mapped_column(
        Text, ForeignKey("companies.synthetic_u", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmCacheRow(Base):
    """Сохранённый ответ функции генератора.

    key — sha256 от имени функции, входных данных, контекста и PROMPT_VERSION
    (services/llm_cache.py); hits — сколько раз ответ отдан из кэша.
    """

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hits: Mapped[int] = mapped_column(Integer, server_default="0")
