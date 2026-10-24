"""PDF-аналитика банка -> Draft (kind=bank_analytics).

Текст — pypdf. Заголовок — из метаданных PDF, иначе первая содержательная строка, иначе имя
файла. Дата — из имени файла, иначе из текста первой страницы, иначе из метаданных
(CreationDate). Метаданные последние: у пересохранённого файла это дата пересохранения,
а не выхода материала. Если текст не извлёкся (скан без текстового слоя) — документ всё
равно сохраняется со статусом empty_text; битый файл — со статусом error. Исключений нет.
"""

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.ingestion.normalizer import Draft, clean_text, clean_title, to_utc

logger = logging.getLogger(__name__)

KIND = "bank_analytics"

_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_TEXT_DATE = re.compile(rf"\b(\d{{1,2}})\s+({'|'.join(_MONTHS)})\s+(\d{{4}})", re.IGNORECASE)
# (?<!\d)…(?!\d), а не \b: в имени файла дата часто стоит после «_», а «_» для \b — буква
_DOTTED_DATE = re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)")
# Даты в именах файлов: год впереди (2026-09-25, 20260925), день впереди (25.09.2026, 25092026),
# месяц словом (15Apr2021, 15_Sep_2026)
_YMD = re.compile(r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")
_DMY = re.compile(r"(?<!\d)(\d{2})[-_.]?(\d{2})[-_.]?(\d{4})(?!\d)")
_EN_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_D_MON_Y = re.compile(
    rf"(?<!\d)(\d{{1,2}})[-_ ]?({'|'.join(_EN_MONTHS)})[a-z]*[-_ ]?(\d{{4}})(?!\d)", re.IGNORECASE
)
# Заголовки из метаданных, которые ничего не говорят о документе
_JUNK_TITLE = re.compile(
    r"^(untitled|без названия|презентация powerpoint)$|\.(docx?|pdf|pptx?)$", re.IGNORECASE
)


def document_url(path: Path) -> str:
    """Постоянный адрес файла: одинаковый на компьютере и в Docker, по нему ищем дубли."""
    return f"local://{path.parent.name}/{path.name}"


def _date(year: int, month: int, day: int) -> datetime | None:
    """Дата без времени -> полночь UTC (чтобы при переводе в UTC не уехать на день назад)."""
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def date_from_text(text: str) -> datetime | None:
    """Первая дата в тексте: «25 сентября 2026» или «25.09.2026»."""
    found = []
    if m := _TEXT_DATE.search(text):
        found.append((m.start(), _date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))))
    if m := _DOTTED_DATE.search(text):
        found.append((m.start(), _date(int(m[3]), int(m[2]), int(m[1]))))
    dates = [d for _, d in sorted(found, key=lambda x: x[0]) if d is not None]
    return dates[0] if dates else None


def _plausible(year: int, month: int, day: int) -> datetime | None:
    """Дата из имени файла, если она существует и год правдоподобен (а не кусок артикула)."""
    return _date(year, month, day) if 1990 <= year <= 2100 else None


def date_from_filename(name: str) -> datetime | None:
    """Дата в имени файла. Восемь цифр подряд — сначала ГГГГММДД, потом ДДММГГГГ."""
    stem = Path(name).stem
    for m in _YMD.finditer(stem):
        if found := _plausible(int(m[1]), int(m[2]), int(m[3])):
            return found
    for m in _DMY.finditer(stem):
        if found := _plausible(int(m[3]), int(m[2]), int(m[1])):
            return found
    if m := _D_MON_Y.search(stem):
        return _plausible(int(m[3]), _EN_MONTHS.index(m[2].lower()) + 1, int(m[1]))
    return None


def _meta_title(reader: PdfReader) -> str:
    title = clean_title(str((reader.metadata or {}).get("/Title") or ""))
    title = re.sub(r"^Microsoft (Word|PowerPoint) - ", "", title)
    return "" if _JUNK_TITLE.search(title) else title


def _first_line(text: str) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if len(line) >= 5:
            return line[:200]
    return ""


def parse_pdf(path: Path, source_name: str, tags: list[str] | None = None) -> Draft:
    """Один PDF -> Draft. Никогда не бросает исключение из-за содержимого файла."""
    data = path.read_bytes()
    draft = Draft(
        kind=KIND,
        source_name=source_name,
        url=document_url(path),
        title=path.stem,
        text="",
        tags=list(tags or []),
        fallback_key=data,
    )
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception as exc:  # одна битая страница не портит весь документ
                logger.warning("PDF %s: страница не прочитана: %s", path.name, exc)
                pages.append("")
        meta_date = (reader.metadata or {}).get("/CreationDate")
        meta_title = _meta_title(reader)
    except Exception as exc:  # pypdf бросает много разных ошибок на битых файлах
        logger.warning("PDF %s не открылся: %s", path.name, exc)
        draft.status = "error"
        return draft

    text = clean_text("\n\n".join(pages))
    draft.text = text
    draft.title = meta_title or _first_line(text) or path.stem
    first_page = clean_text(pages[0]) if pages else ""
    draft.published_at = (
        date_from_filename(path.name)
        or date_from_text(first_page)
        or (to_utc(str(meta_date)) if meta_date else None)
    )
    return draft


def iter_pdf_files(directory: Path) -> Iterator[Path]:
    """PDF-файлы папки (без подпапок) в алфавитном порядке; расширение без учёта регистра."""
    files = (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    # по имени: пути в Windows сравниваются без учёта регистра, порядок зависел бы от ОС
    yield from sorted(files, key=lambda p: p.name)
