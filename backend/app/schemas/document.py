"""Документы из источников — служебный список (internal), не для фронтенда."""

from enum import StrEnum

from pydantic import BaseModel

from app.schemas.common import UtcDatetime

PREVIEW_CHARS = 500


class DocumentKind(StrEnum):
    external_news = "external_news"
    bank_analytics = "bank_analytics"


class DocumentStatus(StrEnum):
    ok = "ok"
    empty_text = "empty_text"
    error = "error"


class Document(BaseModel):
    """Документ без полного текста: начало текста и его длина."""

    id: int
    kind: DocumentKind
    source_name: str
    url: str | None
    title: str
    text_preview: str
    text_length: int
    published_at: UtcDatetime | None
    tags: list[str]
    content_hash: str
    fetched_at: UtcDatetime
    status: DocumentStatus
