from app.schemas.common import Page
from app.schemas.insight import Insight


def _all(client, company_id) -> list[dict]:
    r = client.get(f"/companies/{company_id}/insights", params={"limit": 100})
    return r.json()["items"]


MOCK_COMPANIES = ("c-001", "c-002", "c-003")


def test_every_mock_company_has_6_to_8_varied_insights(client):
    for company_id in MOCK_COMPANIES:
        items = _all(client, company_id)
        assert 6 <= len(items) <= 8
        assert {i["type"] for i in items} == {"risk", "opportunity", "event"}
        assert len({i["importance"] for i in items}) > 3
        assert all(i["company_id"] == company_id for i in items)


def test_company_from_db_has_empty_feed(bank_client):
    """Инсайты на моках до B7: у компаний из БД лента пустая (200), а не ошибка."""
    body = bank_client.get("/companies/U_1/insights").json()
    assert body["items"] == [] and body["total"] == 0
    digest = bank_client.get("/companies/U_1/digest", params={"period": "week"})
    assert digest.status_code == 200 and digest.json()["insight_ids"] == []


def test_refresh_for_company_from_db(bank_client):
    """Агент берёт строку компании из таблицы companies (тестовой БД)."""
    r = bank_client.post("/companies/U_2/insights/refresh")
    assert r.status_code == 202
    job = bank_client.get(f"/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "done", job
    items = bank_client.get("/companies/U_2/insights", params={"limit": 100}).json()["items"]
    assert all(i["company_id"] == "U_2" for i in items)


def test_list_insights_schema(client, company_id):
    r = client.get(f"/companies/{company_id}/insights")
    assert r.status_code == 200
    page = Page[Insight].model_validate(r.json())
    assert page.limit == 20 and page.offset == 0
    assert page.total == len(page.items)


def test_filter_type(client, company_id):
    for t in ("risk", "opportunity", "event"):
        body = client.get(f"/companies/{company_id}/insights", params={"type": t}).json()
        expected = [i for i in _all(client, company_id) if i["type"] == t]
        assert body["total"] == len(expected) > 0
        assert {i["type"] for i in body["items"]} == {t}


def test_filter_min_importance(client, company_id):
    everything = _all(client, company_id)
    threshold = sorted(i["importance"] for i in everything)[len(everything) // 2]
    body = client.get(
        f"/companies/{company_id}/insights", params={"min_importance": threshold}
    ).json()
    expected = [i for i in everything if i["importance"] >= threshold]
    assert body["total"] == len(expected)
    assert all(i["importance"] >= threshold for i in body["items"])
    assert len(expected) < len(everything)  # фильтр что-то отсекает


def test_filter_dates_inclusive(client, company_id):
    everything = _all(client, company_id)
    days = sorted({i["created_at"][:10] for i in everything})
    assert len(days) > 1
    mid = days[len(days) // 2]
    url = f"/companies/{company_id}/insights"
    only_mid = client.get(url, params={"date_from": mid, "date_to": mid}).json()
    assert only_mid["total"] == len([i for i in everything if i["created_at"][:10] == mid]) > 0
    from_mid = client.get(url, params={"date_from": mid}).json()
    assert from_mid["total"] == len([i for i in everything if i["created_at"][:10] >= mid])
    to_mid = client.get(url, params={"date_to": mid}).json()
    assert to_mid["total"] == len([i for i in everything if i["created_at"][:10] <= mid])


def test_filter_dates_nothing_found(client, company_id):
    body = client.get(
        f"/companies/{company_id}/insights",
        params={"date_from": "2000-01-01", "date_to": "2000-01-02"},
    ).json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_combined_filters(client, company_id):
    everything = _all(client, company_id)
    expected = [i for i in everything if i["type"] == "risk" and i["importance"] >= 50]
    body = client.get(
        f"/companies/{company_id}/insights", params={"type": "risk", "min_importance": 50}
    ).json()
    assert body["total"] == len(expected)


def test_pagination(client, company_id):
    url = f"/companies/{company_id}/insights"
    everything = _all(client, company_id)
    first = client.get(url, params={"limit": 3}).json()
    second = client.get(url, params={"limit": 3, "offset": 3}).json()
    assert first["total"] == second["total"] == len(everything)
    assert [i["id"] for i in first["items"] + second["items"]] == [i["id"] for i in everything[:6]]
    beyond = client.get(url, params={"offset": len(everything) + 5}).json()
    assert beyond["items"] == [] and beyond["total"] == len(everything)


def test_sorted_by_created_at_desc(client, company_id):
    dates = [i["created_at"] for i in _all(client, company_id)]
    assert dates == sorted(dates, reverse=True)


def test_invalid_params_422(client, company_id):
    url = f"/companies/{company_id}/insights"
    bad = [
        {"type": "bogus"},
        {"min_importance": 101},
        {"min_importance": -1},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"date_from": "21.10.2026"},
        {"date_to": "2026-13-45"},
    ]
    for params in bad:
        r = client.get(url, params=params)
        assert r.status_code == 422, params
        assert r.json()["error"] == "validation_error"


def test_get_insight(client, insight_id):
    r = client.get(f"/insights/{insight_id}")
    assert r.status_code == 200
    insight = Insight.model_validate(r.json())
    assert insight.id == insight_id
    assert insight.sources
    assert 0 <= insight.importance <= 100
    assert {s.source_kind for s in insight.sources} <= {"external", "bank_analytics"}


def test_insight_not_found(client):
    r = client.get("/insights/nope")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"
