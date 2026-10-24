"""Служебный (internal) список собранных документов — для проверки ingestion, не для фронтенда."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError

from app.schemas.common import Page
from app.schemas.document import Document, DocumentKind
from app.services import document_service

router = APIRouter(prefix="/documents", tags=["internal"])

Limit = Annotated[int, Query(ge=1, le=100, description="Сколько записей вернуть")]
Offset = Annotated[int, Query(ge=0, description="Сколько записей пропустить")]


@router.get("", response_model=Page[Document], summary="[internal] Собранные документы")
def list_documents(
    kind: DocumentKind | None = None,
    date_from: Annotated[
        date | None, Query(description="YYYY-MM-DD, дата публикации по UTC, включительно")
    ] = None,
    date_to: Annotated[
        date | None, Query(description="YYYY-MM-DD, дата публикации по UTC, включительно")
    ] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[Document]:
    """Служебный эндпоинт: документы из RSS и PDF, сначала свежие. Полный текст не отдаётся —
    только начало (text_preview) и длина. С фильтром по датам документы без даты не попадают.
    """
    if date_from and date_to and date_from > date_to:
        raise RequestValidationError(
            [{"loc": ("query", "date_from"), "msg": "date_from позже date_to", "type": "value"}]
        )
    items, total = document_service.list_documents(kind, date_from, date_to, limit, offset)
    return Page[Document](items=items, total=total, limit=limit, offset=offset)
