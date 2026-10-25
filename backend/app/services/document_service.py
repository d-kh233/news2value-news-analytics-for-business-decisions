"""Документы из источников (B4): служебный список для проверки сбора."""

from datetime import UTC, date, datetime, time, timedelta

from app.core.db import get_session
from app.models import Document as DocumentRow
from app.repositories import document_repo
from app.schemas.document import PREVIEW_CHARS, Document, DocumentKind


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _card(row: DocumentRow) -> Document:
    return Document(
        id=row.id,
        kind=row.kind,
        source_name=row.source_name,
        url=row.url,
        title=row.title,
        text_preview=row.text[:PREVIEW_CHARS],
        text_length=len(row.text),
        published_at=row.published_at,
        tags=row.tags,
        content_hash=row.content_hash,
        fetched_at=row.fetched_at,
        status=row.status,
    )


def list_documents(
    kind: DocumentKind | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> tuple[list[Document], int]:
    """date_from и date_to — дни по UTC, оба включительно."""
    with get_session() as session:
        rows, total = document_repo.list_documents(
            session,
            kind=kind.value if kind else None,
            published_from=_day_start(date_from) if date_from else None,
            published_before=_day_start(date_to + timedelta(days=1)) if date_to else None,
            limit=limit,
            offset=offset,
        )
        return [_card(r) for r in rows], total
