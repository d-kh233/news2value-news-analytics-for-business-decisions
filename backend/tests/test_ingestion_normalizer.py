"""Нормализация документов: даты в UTC, чистый текст, content_hash (без БД)."""

import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.ingestion.normalizer import (
    MSK,
    Draft,
    clean_text,
    content_hash,
    normalize,
    parse_offset,
    strip_html,
    to_utc,
)

Z = UTC


def _draft(**kw) -> Draft:
    base = {"kind": "external_news", "source_name": "s", "url": "https://x/1", "title": "T"}
    return Draft(**{**base, "text": "Текст новости", **kw})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # со смещением: переводим в UTC
        (datetime(2026, 10, 20, 10, 15, tzinfo=MSK), datetime(2026, 10, 20, 7, 15, tzinfo=Z)),
        # без пояса — время источника (по умолчанию Москва)
        (datetime(2026, 10, 20, 10, 15), datetime(2026, 10, 20, 7, 15, tzinfo=Z)),
        # feedparser отдаёт struct_time уже в UTC
        (
            time.strptime("2026-10-20 07:15", "%Y-%m-%d %H:%M"),
            datetime(2026, 10, 20, 7, 15, tzinfo=Z),
        ),
        ("Tue, 20 Oct 2026 10:15:00 +0300", datetime(2026, 10, 20, 7, 15, tzinfo=Z)),
        ("Mon, 19 Oct 2026 09:00:00 GMT", datetime(2026, 10, 19, 9, 0, tzinfo=Z)),
        ("2026-10-20T07:15:00Z", datetime(2026, 10, 20, 7, 15, tzinfo=Z)),
        ("2026-10-20T10:15:00+03:00", datetime(2026, 10, 20, 7, 15, tzinfo=Z)),
        # дата из метаданных PDF
        ("D:20260925120000+03'00'", datetime(2026, 9, 25, 9, 0, tzinfo=Z)),
        ("D:20260925120000Z", datetime(2026, 9, 25, 12, 0, tzinfo=Z)),
    ],
)
def test_to_utc(value, expected):
    result = to_utc(value)
    assert result == expected
    assert result.utcoffset() == timedelta(0)


def test_to_utc_naive_uses_source_timezone():
    assert to_utc(datetime(2026, 10, 20, 10, 0), default_tz=UTC) == datetime(
        2026, 10, 20, 10, 0, tzinfo=Z
    )
    minus5 = parse_offset("-05:00")
    assert to_utc("2026-10-20T10:00:00", default_tz=minus5) == datetime(
        2026, 10, 20, 15, 0, tzinfo=Z
    )


@pytest.mark.parametrize("value", [None, "", "вчера вечером", "D:abc", 12345])
def test_to_utc_unparseable_is_none(value):
    assert to_utc(value) is None


def test_parse_offset():
    assert parse_offset("+03:00") == MSK
    assert parse_offset("") == MSK
    assert parse_offset("-0130") == timezone(-timedelta(hours=1, minutes=30))
    with pytest.raises(ValueError):
        parse_offset("MSK")


def test_clean_text():
    raw = "﻿Ключевая ставка  14%\r\nинфля-\nция­ растёт​\x07\n\n\n\n   Второй    абзац\t\tтекста   "
    assert clean_text(raw) == "Ключевая ставка 14%\nинфляция растёт\n\nВторой абзац текста"


def test_clean_text_unicode_forms_are_equal():
    # «й» одной буквой и «и» + знак краткости — после NFC одинаковые
    assert clean_text("Мой") == clean_text("Мой")


def test_strip_html():
    assert strip_html("<p>Рубль&nbsp;ослаб &amp; упал</p>") == " Рубль ослаб & упал "


def test_content_hash_ignores_case_and_spaces():
    assert content_hash("Ключевая  ставка\n14%") == content_hash(" ключевая ставка 14% ")
    assert content_hash("Ключевая ставка 14%") != content_hash("Ключевая ставка 15%")


def test_normalize_ok():
    values = normalize(
        _draft(
            title="  Ставка \n сохранена ",
            text="Ставка  сохранена.",
            published_at="Tue, 20 Oct 2026 10:15:00 +0300",
            tags=["economy", " economy ", "", "Экономика"],
        )
    )
    assert values["status"] == "ok"
    assert values["title"] == "Ставка сохранена"
    assert values["text"] == "Ставка сохранена."
    assert values["published_at"] == datetime(2026, 10, 20, 7, 15, tzinfo=Z)
    assert values["content_hash"] == content_hash("Ставка сохранена.")
    assert values["tags"] == ["economy", "Экономика"]


def test_normalize_empty_text_gets_own_hash():
    a = normalize(_draft(text=" \n  ", fallback_key=b"file-a"))
    b = normalize(_draft(text="", fallback_key=b"file-b"))
    assert a["status"] == b["status"] == "empty_text"
    # у пустых документов хеши разные (иначе второй пустой PDF считался бы дублем первого)
    assert a["content_hash"] != b["content_hash"]
    assert a["content_hash"] != content_hash("")


def test_normalize_keeps_error_status():
    values = normalize(_draft(text="", status="error"))
    assert values["status"] == "error"
    # без fallback_key хеш считается от url
    assert values["content_hash"] != normalize(_draft(text="", url="https://x/2"))["content_hash"]
