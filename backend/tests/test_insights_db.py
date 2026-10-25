"""Лента и карточка инсайтов из БД (B7): фильтры, пагинация, сортировка, пустая лента, источники."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import InsightRow
from app.repositories import document_repo
from app.services import job_service
from tests.conftest import add_news

COMPANY = "U_6"  # строительство жилых зданий (tests/fixtures/companies.csv)


def make_payload(insight_id: str, company_id: str, kind: str, importance: int, day: int,
                 sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    return {
        "id": insight_id,
        "company_id": company_id,
        "title": f"Заголовок {insight_id}",
        "summary": "Суть",
        "type": kind,
        "importance": importance,
        "impact_on_company": "Влияние",
        "recommended_action": "Действие",
        "sources": sources or [],
        "created_at": f"2026-10-{day:02d}T08:00:00Z",
    }


def add_insight(db, insight_id: str, kind: str, importance: int, day: int, docs: list[int],
                company_id: str = COMPANY, sources: list[dict] | None = None) -> None:  # fmt: skip
    payload = make_payload(insight_id, company_id, kind, importance, day, sources)
    with Session(db) as session:
        session.add(
            InsightRow(
                id=insight_id,
                company_id=company_id,
                document_ids=docs,
                payload=payload,
                type=kind,
                importance=importance,
                generated_by="rules",
                created_at=datetime(2026, 10, day, 8, tzinfo=UTC),
            )
        )
        session.commit()


def set_tags(db, doc_id: int, tags: list[str]) -> None:
    with Session(db) as session:
        document_repo.set_tags(session, doc_id, tags)
        session.commit()


@pytest.fixture
def feed(db, bank_client):
    """U_6: 4 инсайта; d_build — новость с тегами economy + okved:41, d_shop — okved:47."""
    d_build = add_news(db, 1, "Застройщики просят продлить ипотеку", "Новостройки.", 20)
    d_shop = add_news(db, 2, "Маркетплейсы открывают ПВЗ", "Магазины.", 21)
    set_tags(db, d_build, ["economy", "okved:41"])
    set_tags(db, d_shop, ["okved:47"])
    add_insight(db, "i1", "risk", 90, 20, [d_build])
    add_insight(db, "i2", "opportunity", 70, 21, [d_shop])
    add_insight(db, "i3", "event", 70, 22, [])
    add_insight(db, "i4", "risk", 40, 18, [d_build, d_shop])
    return {"client": bank_client, "build": d_build, "shop": d_shop}


def _ids(client, **params) -> list[str]:
    r = client.get(f"/companies/{COMPANY}/insights", params=params)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def test_sorted_by_importance_then_date(feed):
    assert _ids(feed["client"]) == ["i1", "i3", "i2", "i4"]


def test_filter_type(feed):
    assert _ids(feed["client"], type="risk") == ["i1", "i4"]


def test_filter_min_importance(feed):
    assert _ids(feed["client"], min_importance=70) == ["i1", "i3", "i2"]


def test_filter_dates_inclusive(feed):
    assert _ids(feed["client"], date_from="2026-10-21") == ["i3", "i2"]
    assert _ids(feed["client"], date_to="2026-10-20") == ["i1", "i4"]
    assert _ids(feed["client"], date_from="2026-10-20", date_to="2026-10-20") == ["i1"]


def test_filter_industry(feed):
    c = feed["client"]
    assert _ids(c, industry="41") == ["i1", "i4"]
    assert _ids(c, industry="OKVED:41") == ["i1", "i4"]
    assert _ids(c, industry="okved:47") == ["i2", "i4"]
    assert _ids(c, industry="Economy") == ["i1", "i4"]
    assert _ids(c, industry="okved:01") == []


def test_filters_combined(feed):
    assert _ids(feed["client"], type="risk", industry="47") == ["i4"]
    assert _ids(feed["client"], min_importance=50, industry="41", date_to="2026-10-20") == ["i1"]


def test_pagination_and_total(feed):
    c = feed["client"]
    body = c.get(f"/companies/{COMPANY}/insights", params={"limit": 2, "offset": 1}).json()
    assert [i["id"] for i in body["items"]] == ["i3", "i2"]
    assert (body["total"], body["limit"], body["offset"]) == (4, 2, 1)
    body = c.get(f"/companies/{COMPANY}/insights", params={"type": "risk", "limit": 1}).json()
    assert body["total"] == 2 and len(body["items"]) == 1
    assert c.get(f"/companies/{COMPANY}/insights", params={"offset": 10}).json()["items"] == []


def test_filters_found_nothing_no_hint(feed):
    body = feed["client"].get(f"/companies/{COMPANY}/insights", params={"type": "event",
                              "min_importance": 99}).json()  # fmt: skip
    assert body["items"] == [] and body["total"] == 0 and body["hint"] is None


def test_empty_feed_has_hint(bank_client):
    body = bank_client.get("/companies/U_1/insights").json()
    assert body["items"] == [] and body["total"] == 0
    assert body["hint"] == "Инсайты ещё не собраны, нажмите «Обновить»"
    assert body["refresh_job_id"] is None


def test_empty_feed_while_refresh_running(bank_client):
    job_id = job_service.request_refresh("U_1", None)
    body = bank_client.get("/companies/U_1/insights").json()
    assert body["hint"] == "Инсайты собираются…" and body["refresh_job_id"] == job_id


def test_refresh_job_id_with_insights(feed):
    job_id = job_service.request_refresh(COMPANY, None)
    body = feed["client"].get(f"/companies/{COMPANY}/insights").json()
    assert body["total"] == 4 and body["hint"] is None and body["refresh_job_id"] == job_id


def test_404_and_422(feed):
    c = feed["client"]
    r = c.get("/companies/NOPE/insights")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "detail": "Компания NOPE не найдена"}
    r = c.get("/insights/nope")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert "Инсайт nope не найден" in r.json()["detail"]
    r = c.get(f"/companies/{COMPANY}/insights", params={"date_from": "2026-10-22",
              "date_to": "2026-10-20"})  # fmt: skip
    assert r.status_code == 422 and r.json()["error"] == "validation_error"
    assert "date_from позже date_to" in r.json()["detail"]
    assert c.get(f"/companies/{COMPANY}/insights", params={"type": "bad"}).status_code == 422


def test_mock_company_is_404_without_mocks(bank_client):
    assert bank_client.get("/companies/c-001/insights").status_code == 404


def test_card_sources_from_documents(db, bank_client):
    doc = add_news(db, 1, "ЦБ сохранил ставку", "Текст.", 21)
    extra = add_news(db, 2, "Связанная новость", "Текст.", 20)
    sources = [
        {"id": f"N:doc-{doc}", "title": "старый заголовок", "url": "https://old",
         "published_at": "2026-01-01T00:00:00Z", "source_kind": "external"},
        {"id": "A:BA-01:0", "title": "Тезис банка", "url": "",
         "published_at": "2026-10-01T00:00:00Z", "source_kind": "bank_analytics"},
        {"title": "Моковая новость агента", "url": "https://mock",
         "published_at": "2026-10-02T00:00:00Z", "source_kind": "external"},
    ]  # fmt: skip
    add_insight(db, "card", "risk", 80, 22, [doc, extra], sources=sources)
    body = bank_client.get("/insights/card").json()
    got = [(s.get("id"), s["title"], s["url"], s["published_at"]) for s in body["sources"]]
    site = "https://news.example.com"
    assert got == [
        (f"N:doc-{doc}", "ЦБ сохранил ставку", f"{site}/1", "2026-10-21T09:00:00Z"),
        ("A:BA-01:0", "Тезис банка", "", "2026-10-01T00:00:00Z"),
        (None, "Моковая новость агента", "https://mock", "2026-10-02T00:00:00Z"),
        (f"N:doc-{extra}", "Связанная новость", f"{site}/2", "2026-10-20T09:00:00Z"),
    ]
    assert body["sources"][0]["source_kind"] == "external"
    # в ленте — источники из payload как есть (без запроса к documents)
    item = bank_client.get(f"/companies/{COMPANY}/insights").json()["items"][0]
    assert item["sources"][0]["title"] == "старый заголовок"


def test_use_mocks_returns_old_behaviour(bank_client, env):
    env(USE_MOCKS="true")
    body = bank_client.get("/companies/c-001/insights").json()
    assert body["total"] >= 6
    dates = [i["created_at"] for i in body["items"]]
    assert dates == sorted(dates, reverse=True)  # у моков — свежие первыми, как до B7
    assert bank_client.get(f"/insights/{body['items'][0]['id']}").status_code == 200
    assert bank_client.get("/companies/U_1/insights").json()["hint"] is None
