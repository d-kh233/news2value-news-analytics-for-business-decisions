"""GET /documents (служебный) и news_provider для агента — на тестовых документах в БД."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.ingestion.normalizer import content_hash
from app.main import app
from app.ml_ports import agent_port, news_port
from app.ml_ports.news_port import news_provider
from app.repositories import document_repo

NEWS_KEYS = {"id", "title", "text", "published_at", "url", "source", "tags"}


def _add(db: Engine, n: int, published: datetime | None, **overrides) -> int:
    """Документ №n; text/url уникальны по n."""
    values = {
        "kind": "external_news",
        "source_name": "test_feed",
        "url": f"https://news.example.com/{n}",
        "title": f"Новость {n}",
        "text": f"Текст новости номер {n}. " * 50,
        "published_at": published,
        "tags": ["economy"],
        "status": "ok",
        **overrides,
    }
    values["content_hash"] = content_hash(values["text"] + values["url"])
    with Session(db) as session:
        doc_id = document_repo.insert_new(session, values)
        session.commit()
    assert doc_id is not None
    return doc_id


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# ---------- GET /documents ----------


@pytest.fixture
def docs(db: Engine) -> dict[str, int]:
    return {
        "news_oct20": _add(db, 1, _utc(2026, 10, 20, 7, 15)),
        "news_oct21": _add(db, 2, _utc(2026, 10, 21, 23, 59)),
        "pdf_sep25": _add(
            db, 3, _utc(2026, 9, 25, 9), kind="bank_analytics", url="local://analytics/a.pdf"
        ),
        "news_no_date": _add(db, 4, None),
    }


def test_list_documents(db_client, docs):
    resp = db_client.get("/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4 and body["limit"] == 20 and body["offset"] == 0
    # сначала свежие, документы без даты — в конце
    assert [d["id"] for d in body["items"]] == [
        docs["news_oct21"],
        docs["news_oct20"],
        docs["pdf_sep25"],
        docs["news_no_date"],
    ]
    item = body["items"][0]
    assert item["published_at"] == "2026-10-21T23:59:00Z"
    assert item["fetched_at"].endswith("Z")
    assert len(item["text_preview"]) == 500 and item["text_length"] > 500
    assert "text" not in item
    assert body["items"][-1]["published_at"] is None


def test_list_documents_filters(db_client, docs):
    kind = db_client.get("/documents", params={"kind": "bank_analytics"}).json()
    assert [d["id"] for d in kind["items"]] == [docs["pdf_sep25"]]

    # обе даты включительно, по UTC; документы без даты с фильтром не попадают
    period = db_client.get(
        "/documents", params={"date_from": "2026-10-20", "date_to": "2026-10-21"}
    ).json()
    assert {d["id"] for d in period["items"]} == {docs["news_oct20"], docs["news_oct21"]}

    one_day = db_client.get("/documents", params={"date_to": "2026-10-20"}).json()
    assert {d["id"] for d in one_day["items"]} == {docs["news_oct20"], docs["pdf_sep25"]}

    page = db_client.get("/documents", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 4 and [d["id"] for d in page["items"]] == [docs["news_oct20"]]


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-10-22", "date_to": "2026-10-21"},
        {"kind": "tweet"},
        {"date_from": "20.10.2026"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
def test_list_documents_bad_params(db_client, params):
    resp = db_client.get("/documents", params=params)
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"


def test_documents_marked_internal():
    op = app.openapi()["paths"]["/documents"]["get"]
    assert op["tags"] == ["internal"]
    assert "internal" in op["summary"]


# ---------- news_provider ----------


@pytest.fixture
def news_db(db: Engine, monkeypatch) -> Engine:
    """news_provider ходит в тестовую БД; «сегодня» агента — 2026-10-22 (см. conftest)."""
    from app.core import db as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: db)
    return db


def test_news_provider_window_and_format(news_db):
    inside = _add(news_db, 1, _utc(2026, 10, 21, 7, 15))
    first_day = _add(news_db, 2, _utc(2026, 10, 15, 0, 0))
    # без даты публикации — по времени сбора
    no_date = _add(news_db, 3, None, fetched_at=_utc(2026, 10, 20, 12))
    today_late = _add(news_db, 4, _utc(2026, 10, 22, 23, 59))
    _add(news_db, 5, _utc(2026, 10, 14, 23, 59))  # день до окна
    _add(news_db, 6, _utc(2026, 10, 23, 0, 0))  # завтра
    _add(news_db, 7, _utc(2026, 10, 21), kind="bank_analytics")  # аналитика — другим каналом
    _add(news_db, 8, _utc(2026, 10, 21), status="empty_text", text="")

    items = news_provider("U_1", 7)

    assert [i["id"] for i in items] == [
        f"doc-{today_late}",
        f"doc-{inside}",
        f"doc-{no_date}",
        f"doc-{first_day}",
    ]
    for item in items:
        assert set(item) == NEWS_KEYS
    item = items[1]
    assert item["published_at"] == "2026-10-21T07:15:00Z"
    assert item["source"] == "test_feed"
    assert item["url"] == "https://news.example.com/1"
    assert item["tags"] == ["economy"]
    assert items[2]["published_at"] == "2026-10-20T12:00:00Z"


def test_news_provider_items_parse_as_agent_news(news_db):
    """Элементы читает агент: agent.schemas.NewsItem.from_dict и Tools.get_relevant_news."""
    doc_id = _add(news_db, 1, _utc(2026, 10, 21, 7, 15))
    agent_port._ensure_repo_on_path()
    from datetime import date

    from agent.schemas import NewsItem
    from agent.tools import Tools

    news = NewsItem.from_dict(news_provider("U_1", 7)[0])
    assert news.news_id == f"doc-{doc_id}"
    assert news.sid == f"N:doc-{doc_id}"
    assert news.published_at == date(2026, 10, 21)
    assert news.source == "test_feed"
    assert news.event is None

    found, source = Tools(date(2026, 10, 22), news_provider).get_relevant_news("U_1", 7)
    assert source == "provider"
    assert [n.news_id for n in found] == [f"doc-{doc_id}"]


def test_news_provider_period_zero_is_today_only(news_db):
    today = _add(news_db, 1, _utc(2026, 10, 22, 0, 0))
    _add(news_db, 2, _utc(2026, 10, 21, 23, 59))
    assert [i["id"] for i in news_provider("U_1", 0)] == [f"doc-{today}"]
    with pytest.raises(ValueError):
        news_provider("U_1", -1)


def test_news_provider_limit(news_db):
    for n in range(105):
        _add(news_db, n, _utc(2026, 10, 1 + n % 20, n % 24))
    items = news_provider("U_1", 30)
    assert len(items) == news_port.NEWS_LIMIT == 100
    stamps = [i["published_at"] for i in items]
    assert stamps == sorted(stamps, reverse=True)


def test_news_provider_today_from_env(news_db, monkeypatch):
    doc = _add(news_db, 1, _utc(2026, 9, 1, 10))
    assert news_provider("U_1", 7) == []
    monkeypatch.setenv("AGENT_TODAY", "2026-09-03")
    assert [i["id"] for i in news_provider("U_1", 7)] == [f"doc-{doc}"]


def test_select_news_provider():
    # NEWS_SOURCE=mock (по умолчанию) — агент берёт свои моки; db — таблица documents
    assert agent_port.select_news_provider("mock") is None
    assert agent_port.select_news_provider("db") is news_provider
