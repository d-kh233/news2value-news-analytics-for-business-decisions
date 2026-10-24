"""Собранные документы (модуль B4): внешние новости из RSS и PDF-аналитика банка."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

KINDS = ("external_news", "bank_analytics")
STATUSES = ("ok", "empty_text", "error")


class Document(Base):
    """Один документ: текст уже нормализован (app/ingestion/normalizer.py).

    content_hash — sha256 нормализованного текста, по нему ловим дубли с разными адресами.
    У документов без текста (empty_text, error) хеш считается от байтов файла или от url,
    иначе все пустые документы считались бы дублями друг друга.
    url уникален, если задан (частичный уникальный индекс — только для строк с url).
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(f"kind IN {KINDS}", name="ck_documents_kind"),
        CheckConstraint(f"status IN {STATUSES}", name="ck_documents_status"),
        UniqueConstraint("content_hash", name="uq_documents_content_hash"),
        Index("uq_documents_url", "url", unique=True, postgresql_where=sql_text("url IS NOT NULL")),
        Index("ix_documents_kind_published_at", "kind", "published_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20))
    source_name: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, server_default="")
    text: Mapped[str] = mapped_column(Text, server_default="")
    # NULL — источник не указал дату публикации
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"))
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(12))
