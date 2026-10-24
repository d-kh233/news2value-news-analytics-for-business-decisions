"""Документы (таблица documents): запись при сборе, выборки для API и для агента."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Document


def find_by_url(session: Session, url: str) -> Document | None:
    return session.scalar(select(Document).where(Document.url == url))


def hash_taken(session: Session, content_hash: str, except_id: int | None = None) -> bool:
    """Есть ли другой документ с таким content_hash."""
    stmt = select(Document.id).where(Document.content_hash == content_hash)
    if except_id is not None:
        stmt = stmt.where(Document.id != except_id)
    return session.scalar(stmt.limit(1)) is not None


def insert_new(session: Session, values: dict[str, Any]) -> int | None:
    """Вставить документ; None — такой url или content_hash уже есть (строка не создана)."""
    stmt = insert(Document).values(**values).on_conflict_do_nothing().returning(Document.id)
    return session.scalar(stmt)


def replace(session: Session, doc_id: int, values: dict[str, Any]) -> None:
    """Перезаписать документ (повторный разбор того, что раньше не получилось)."""
    session.execute(
        update(Document).where(Document.id == doc_id).values(**values, fetched_at=func.now())
    )


def list_documents(
    session: Session,
    kind: str | None = None,
    published_from: datetime | None = None,
    published_before: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Document], int]:
    """Страница документов (сначала свежие) и общее число найденных.

    published_from включительно, published_before — не включительно. Если задана хотя бы одна
    граница, документы без даты публикации не попадают в выборку.
    """
    stmt = select(Document)
    if kind is not None:
        stmt = stmt.where(Document.kind == kind)
    if published_from is not None:
        stmt = stmt.where(Document.published_at >= published_from)
    if published_before is not None:
        stmt = stmt.where(Document.published_at < published_before)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = (
        stmt.order_by(Document.published_at.desc().nulls_last(), Document.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(page)), total


def recent_news(session: Session, since: datetime, before: datetime, limit: int) -> list[Document]:
    """Внешние новости с текстом за [since, before), сначала свежие.

    Если источник не дал дату публикации, берём время сбора (fetched_at).
    """
    when = func.coalesce(Document.published_at, Document.fetched_at)
    stmt = (
        select(Document)
        .where(
            Document.kind == "external_news",
            Document.status == "ok",
            when >= since,
            when < before,
        )
        .order_by(when.desc(), Document.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def existing_ids(session: Session, ids: list[int]) -> set[int]:
    """Какие из id есть в documents (проверка ссылок инсайтов на документы)."""
    if not ids:
        return set()
    return set(session.scalars(select(Document.id).where(Document.id.in_(ids))))
