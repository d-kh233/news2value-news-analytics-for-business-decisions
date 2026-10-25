"""Кэш ответов генератора (таблица llm_cache)."""

from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import LlmCacheRow


def take(session: Session, key: str) -> Any | None:
    """Сохранённый ответ и +1 к hits; None — ключа нет (null в кэш не кладём)."""
    stmt = (
        update(LlmCacheRow)
        .where(LlmCacheRow.key == key)
        .values(hits=LlmCacheRow.hits + 1)
        .returning(LlmCacheRow.value)
    )
    return session.execute(stmt).scalar_one_or_none()


def put(session: Session, key: str, value: Any) -> None:
    """Сохранить ответ; если ключ уже записал другой процесс — оставить его запись."""
    stmt = insert(LlmCacheRow).values(key=key, value=value).on_conflict_do_nothing()
    session.execute(stmt)
