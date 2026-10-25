"""Журнал audit_log."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.models import AuditRow


def add(session: Session, values: dict[str, Any]) -> None:
    session.execute(insert(AuditRow).values(**values))


def tokens_since(session: Session, since: datetime) -> int:
    """Сколько токенов модели потрачено с момента since (по записям с tokens_used)."""
    total = session.scalar(
        select(func.coalesce(func.sum(AuditRow.tokens_used), 0)).where(AuditRow.ts >= since)
    )
    return int(total or 0)


def list_recent(session: Session, limit: int = 100) -> list[AuditRow]:
    return list(session.scalars(select(AuditRow).order_by(AuditRow.id.desc()).limit(limit)))
