"""Журнал действий (B8+B9): вызовы ML-функций и входы пользователей."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditRow(Base):
    """Одна запись журнала.

    action — что произошло: ml_call (вызов ML-функции) или login (вход);
    tool — какая функция (agent.generate_insights, llm.digest…), у входа — auth.login;
    input_refs — только id входных данных (компания, инсайт, doc-N), не тексты;
    status — ok | cache_hit (ответ из llm_cache) | error | limit (дневной лимит токенов) у
    ML, ok | failed | error у входа; tokens_used — сколько токенов модели потратил вызов, если
    известно. user_id пустой у фоновых скриптов. Внешних ключей нет: журнал переживает
    удаление компаний и пользователей.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_company_ts", "company_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    user_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(20))
    tool: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[str | None] = mapped_column(Text)
    input_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(12))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
