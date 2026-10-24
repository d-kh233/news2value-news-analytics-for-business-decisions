"""Запуск сбора по config/sources.yaml и отчёт.

Каждый источник обрабатывается отдельно: если он упал целиком (лента недоступна, нет
папки), причина пишется в отчёт, остальные источники продолжают работать.

Дубли не сохраняются, а считаются:
  • dup_url — документ с таким адресом уже есть (статья или файл уже собраны);
  • dup_hash — тот же текст уже лежит под другим адресом (content_hash).
Документ, который раньше не получилось разобрать (empty_text, error), при повторном запуске
разбирается снова; если теперь текст есть — строка обновляется на месте (updated), новой
строки не появляется.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.ingestion import pdf_parser, rss_parser
from app.ingestion.normalizer import Draft, normalize, parse_offset
from app.ingestion.rss_parser import PoliteClient
from app.repositories import document_repo

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "News2ValueBot/0.1 (+https://github.com/d-kh233/news2value-news-analytics-for-business-decisions)"


class _Source(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)
    # пояс для дат без указания пояса; по умолчанию московское время
    tz_offset: str = "+03:00"

    @field_validator("tz_offset")
    @classmethod
    def _check_offset(cls, value: str) -> str:
        parse_offset(value)
        return value

    @property
    def tz(self) -> timezone:
        return parse_offset(self.tz_offset)


class PdfSource(_Source):
    dir: Path


class RssSource(_Source):
    url: str
    max_items: int = Field(20, ge=1)


class SourcesConfig(BaseModel):
    user_agent: str = DEFAULT_USER_AGENT
    timeout_s: float = Field(10.0, gt=0)
    delay_s: float = Field(1.0, ge=0)
    pdf: list[PdfSource] = Field(default_factory=list)
    rss: list[RssSource] = Field(default_factory=list)


def load_sources(path: Path) -> SourcesConfig:
    """Прочитать sources.yaml. Относительные папки PDF — от корня репозитория (папка над config/).

    В контейнере файл смонтирован в /config/sources.yaml, поэтому data/raw/analytics там —
    это /data/raw/analytics.
    """
    config = SourcesConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    base = path.resolve().parent.parent
    for src in config.pdf:
        if not src.dir.is_absolute():
            src.dir = base / src.dir
    return config


@dataclass
class SourceReport:
    name: str
    kind: str  # pdf | rss
    found: int = 0
    new: int = 0
    updated: int = 0
    dup_url: int = 0
    dup_hash: int = 0
    # среди новых и обновлённых: без текста и с ошибкой
    empty_text: int = 0
    errors: int = 0
    # причина, если источник упал целиком
    failed: str | None = None


@dataclass
class IngestReport:
    sources: list[SourceReport] = field(default_factory=list)

    def total(self, attr: str) -> int:
        return sum(getattr(s, attr) for s in self.sources)

    @property
    def failed_sources(self) -> list[SourceReport]:
        return [s for s in self.sources if s.failed]


def _save(session: Session, values: dict[str, Any]) -> str:
    """Записать документ; вернуть new | updated | dup_url | dup_hash."""
    url = values["url"]
    if url:
        existing = document_repo.find_by_url(session, url)
        if existing is not None:
            # уже собран нормально или снова без текста — оставляем как есть
            if existing.status == "ok" or values["status"] != "ok":
                return "dup_url"
            if document_repo.hash_taken(session, values["content_hash"], existing.id):
                return "dup_hash"
            document_repo.replace(session, existing.id, values)
            return "updated"
    if document_repo.insert_new(session, values) is None:
        return "dup_hash"
    return "new"


def _already_ok(session: Session, url: str) -> bool:
    """Документ по адресу уже собран с текстом — повторно не скачиваем и не разбираем."""
    doc = document_repo.find_by_url(session, url)
    return doc is not None and doc.status == "ok"


def _store(session: Session, report: SourceReport, draft: Draft, tz: timezone) -> None:
    values = normalize(draft, tz)
    outcome = _save(session, values)
    session.commit()
    setattr(report, outcome, getattr(report, outcome) + 1)
    if outcome in ("new", "updated"):
        if values["status"] == "empty_text":
            report.empty_text += 1
        elif values["status"] == "error":
            report.errors += 1


def _item(session: Session, report: SourceReport, what: str, work: Callable[[], None]) -> None:
    """Один документ: его ошибка считается, но не останавливает источник."""
    try:
        work()
    except Exception:
        session.rollback()
        report.errors += 1
        logger.exception("%s: документ %s не сохранён", report.name, what)


def ingest_pdf_source(engine: Engine, src: PdfSource, report: SourceReport) -> None:
    files = list(pdf_parser.iter_pdf_files(src.dir))
    report.found = len(files)
    with Session(engine) as session:
        for path in files:
            if _already_ok(session, pdf_parser.document_url(path)):
                report.dup_url += 1
                continue

            def work(path: Path = path) -> None:
                _store(session, report, pdf_parser.parse_pdf(path, src.name, src.tags), src.tz)

            _item(session, report, path.name, work)


def ingest_rss_source(
    engine: Engine, src: RssSource, client: PoliteClient, report: SourceReport
) -> None:
    entries = rss_parser.read_feed(client, src.url, src.max_items)
    report.found = len(entries)
    with Session(engine) as session:
        for entry in entries:
            if _already_ok(session, entry.url):
                report.dup_url += 1
                continue

            def work(entry: rss_parser.FeedEntry = entry) -> None:
                draft = rss_parser.fetch_article(client, entry, src.name, src.tags)
                _store(session, report, draft, src.tz)

            _item(session, report, entry.url, work)


# Ожидаемые причины падения источника: в лог одной строкой, без трейсбека
_EXPECTED_FAILURES = (
    httpx.HTTPError,
    rss_parser.Disallowed,
    rss_parser.FeedError,
    FileNotFoundError,
    NotADirectoryError,
)


def _failure(exc: Exception) -> str:
    """Причина для отчёта одной строкой (у ошибок httpx текст многострочный)."""
    first_line = (str(exc).splitlines() or [""])[0]
    return f"{type(exc).__name__}: {first_line}"[:300]


def _run_source(name: str, kind: str, work: Callable[[SourceReport], None]) -> SourceReport:
    report = SourceReport(name=name, kind=kind)
    try:
        work(report)
    except _EXPECTED_FAILURES as exc:
        report.failed = _failure(exc)
        logger.warning("Источник %s упал: %s", name, report.failed)
    except Exception as exc:
        report.failed = _failure(exc)
        logger.exception("Источник %s упал", name)
    return report


def run_ingestion(
    engine: Engine,
    config: SourcesConfig,
    *,
    pdf: bool = True,
    rss: bool = True,
    client: PoliteClient | None = None,
) -> IngestReport:
    """Собрать документы из источников config. client — свой HTTP-клиент (для тестов)."""
    report = IngestReport()
    if pdf:
        for p in config.pdf:
            report.sources.append(
                _run_source(p.name, "pdf", lambda r, p=p: ingest_pdf_source(engine, p, r))
            )
    if rss and config.rss:
        own_client = client is None
        http = client or PoliteClient(config.user_agent, config.timeout_s, config.delay_s)
        try:
            for s in config.rss:
                report.sources.append(
                    _run_source(s.name, "rss", lambda r, s=s: ingest_rss_source(engine, s, http, r))
                )
        finally:
            if own_client:
                http.close()
    return report


_COLUMNS = [
    ("found", "найдено"),
    ("new", "новых"),
    ("updated", "обновлено"),
    ("dup_url", "дубли url"),
    ("dup_hash", "дубли хеш"),
    ("empty_text", "без текста"),
    ("errors", "ошибок"),
]


def format_report(report: IngestReport) -> str:
    width = max([len(s.name) for s in report.sources] + [len("источник")])
    head = f"{'источник':<{width}}  тип  " + "  ".join(title for _, title in _COLUMNS)
    lines = ["Сбор документов", head, "-" * len(head)]
    for s in report.sources:
        prefix = f"{s.name:<{width}}  {s.kind:<3}  "
        if s.failed:
            lines.append(f"{prefix}ИСТОЧНИК УПАЛ: {s.failed}")
            continue
        cells = "  ".join(f"{getattr(s, attr):>{len(title)}}" for attr, title in _COLUMNS)
        lines.append(prefix + cells)
    if not report.sources:
        lines.append("(нет источников)")
    totals = ", ".join(f"{title} {report.total(attr)}" for attr, title in _COLUMNS)
    lines += [
        "",
        f"Итого: {totals}",
        f"Источников: {len(report.sources)}, упало: {len(report.failed_sources)}",
    ]
    return "\n".join(lines)
