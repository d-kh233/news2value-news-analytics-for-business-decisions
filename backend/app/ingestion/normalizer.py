"""Нормализация документа перед записью: даты в UTC, чистый текст, content_hash.

Парсеры (pdf_parser, rss_parser) отдают Draft — «сырой» документ как есть из источника.
normalize() превращает его в значения колонок таблицы documents.
"""

import calendar
import hashlib
import html
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

# Московское время без перехода на летнее (с 2014 года) — фиксированное смещение +3.
# zoneinfo не используем: в Windows без пакета tzdata он не знает часовых поясов.
MSK = timezone(timedelta(hours=3))

# Дата из метаданных PDF: D:20260825143000+03'00'
_PDF_DATE = re.compile(
    r"^D:(?P<y>\d{4})(?P<m>\d{2})?(?P<d>\d{2})?(?P<H>\d{2})?(?P<M>\d{2})?(?P<S>\d{2})?"
    r"(?P<tz>Z|[+-]\d{2}'?\d{2}'?)?"
)
_OFFSET = re.compile(r"^(?P<sign>[+-])(?P<h>\d{2}):?(?P<m>\d{2})$")

# Невидимые и «особые» пробелы, которые превращаем в обычный пробел
_SPACES = dict.fromkeys(map(ord, "     　\t\f\v"), " ")
# Мягкий перенос и символы нулевой ширины просто убираем
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
# Перенос слова по слогам в PDF: «инфля-\nция» -> «инфляция»
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_TAG = re.compile(r"<[^>]+>")


@dataclass
class Draft:
    """Документ из источника до нормализации."""

    kind: str
    source_name: str
    url: str | None
    title: str
    text: str
    # datetime, time.struct_time (feedparser, уже в UTC), строка или None
    published_at: Any = None
    tags: list[str] = field(default_factory=list)
    # парсер ставит error, если не смог получить документ; empty_text решает normalize()
    status: str = "ok"
    # из чего считать хеш, если текста нет: байты файла или url
    fallback_key: bytes | str = b""


def parse_offset(value: str) -> timezone:
    """«+03:00» / «-0500» -> timezone. Пустая строка — московское время."""
    if not value:
        return MSK
    m = _OFFSET.match(value.strip())
    if not m:
        raise ValueError(f"Смещение часового пояса в формате +HH:MM, получено {value!r}")
    delta = timedelta(hours=int(m["h"]), minutes=int(m["m"]))
    return timezone(-delta if m["sign"] == "-" else delta)


def _parse_pdf_date(value: str) -> datetime | None:
    m = _PDF_DATE.match(value.strip())
    if not m:
        return None
    parts = {k: int(m[k] or default) for k, default in (("y", 0), ("m", 1), ("d", 1))}
    parts.update({k: int(m[k] or 0) for k in ("H", "M", "S")})
    dt = datetime(parts["y"], parts["m"], parts["d"], parts["H"], parts["M"], parts["S"])
    tz = m["tz"]
    if tz == "Z":
        return dt.replace(tzinfo=UTC)
    if tz:
        digits = tz.replace("'", "")
        return dt.replace(tzinfo=parse_offset(f"{digits[0]}{digits[1:3]}:{digits[3:5]}"))
    return dt


def _parse_string_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    if value.startswith("D:"):
        return _parse_pdf_date(value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        # формат RSS (RFC 2822): Tue, 20 Oct 2026 07:15:00 +0300
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


def to_utc(value: Any, default_tz: timezone = MSK) -> datetime | None:
    """Любая дата из источника -> datetime в UTC или None, если разобрать нельзя.

    Дата без часового пояса считается временем источника (default_tz).
    struct_time от feedparser уже переведён в UTC.
    """
    if value is None:
        return None
    if isinstance(value, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
    if isinstance(value, str):
        value = _parse_string_date(value)
        if value is None:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=default_tz)
    return value.astimezone(UTC)


def strip_html(value: str) -> str:
    """HTML-фрагмент (описание из RSS) -> текст: теги убираем, сущности (&amp;) раскрываем."""
    return html.unescape(_TAG.sub(" ", value or ""))


def clean_text(value: str) -> str:
    """Чистый текст: абзацы сохраняются, лишние пробелы и служебные символы — нет."""
    value = unicodedata.normalize("NFC", value or "")
    value = value.translate(_INVISIBLE).translate(_SPACES)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    # управляющие символы (кроме перевода строки) — мусор из PDF
    value = "".join(ch for ch in value if ch == "\n" or unicodedata.category(ch) != "Cc")
    value = _HYPHEN_BREAK.sub(r"\1\2", value)
    lines = [" ".join(line.split()) for line in value.split("\n")]
    # подряд идущие пустые строки -> одна (граница абзаца)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip()


def clean_title(value: str) -> str:
    """Заголовок в одну строку."""
    return " ".join(clean_text(value).split())


def content_hash(text: str) -> str:
    """sha256 нормализованного текста: регистр и пробелы на хеш не влияют."""
    key = " ".join(text.casefold().split())
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def fallback_hash(key: bytes | str) -> str:
    """Хеш документа без текста. Префикс — чтобы не совпасть с хешем настоящего текста."""
    data = key.encode("utf-8") if isinstance(key, str) else key
    return hashlib.sha256(b"no-text:" + data).hexdigest()


def normalize(draft: Draft, default_tz: timezone = MSK) -> dict[str, Any]:
    """Draft -> значения колонок documents (кроме id и fetched_at)."""
    text = clean_text(draft.text)
    status = draft.status
    if status == "ok" and not text:
        status = "empty_text"
    if status == "ok":
        digest = content_hash(text)
    else:
        digest = fallback_hash(draft.fallback_key or draft.url or draft.title)
    return {
        "kind": draft.kind,
        "source_name": draft.source_name,
        "url": draft.url,
        "title": clean_title(draft.title),
        "text": text,
        "published_at": to_utc(draft.published_at, default_tz),
        # без повторов, порядок сохраняется
        "tags": list(dict.fromkeys(t.strip() for t in draft.tags if t and t.strip())),
        "content_hash": digest,
        "status": status,
    }
