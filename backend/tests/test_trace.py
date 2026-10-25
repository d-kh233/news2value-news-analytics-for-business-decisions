"""GET /insights/{id}/trace (B8+B9): запуск, шаги со временем, источники, признаки профиля."""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import InsightRow
from app.services.refresh import run_for_company

DRINKS = "U_2"  # совпадает с новостью drinks из фикстуры news


def test_trace_after_refresh(news, modes, bank_client):
    modes("fake", "db")
    result = run_for_company(DRINKS, "cfo", "week")
    insight_id = result.insights[0].id

    r = bank_client.get(f"/insights/{insight_id}/trace")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["run_id"] == result.run_id
    assert t["run_status"] == "done"
    assert t["generator_run_id"]
    assert t["role"] == "cfo"
    assert t["generated_by"] == "fake"
    assert t["prompt_version"] == "fake@v1"
    assert t["used_profile_fields"]
    assert t["duration_ms"] is not None and t["duration_ms"] >= 0
    assert [s["name"] for s in t["steps"]] == ["Поиск новостей", "Совпадение слов с профилем"]
    assert all(isinstance(s["duration_ms"], int) for s in t["steps"])
    doc = news["drinks"]
    assert t["sources"][0]["document_id"] == doc
    assert t["sources"][0]["source_name"] == "test_feed"
    assert t["sources"][0]["id"] == f"N:doc-{doc}"


def test_trace_of_insight_without_run(db, bank_client):
    """Инсайт без запуска (например, сохранён до миграции 0007): шагов нет, run_id = null."""
    payload = {
        "id": "old-1",
        "company_id": DRINKS,
        "title": "Старый",
        "summary": "Суть",
        "type": "event",
        "importance": 50,
        "impact_on_company": "Влияние",
        "recommended_action": "Действие",
        "sources": [],
        "created_at": "2026-10-20T08:00:00Z",
    }
    with Session(db) as session:
        session.add(
            InsightRow(
                id="old-1",
                company_id=DRINKS,
                payload=payload,
                type="event",
                importance=50,
                generated_by="rules",
                created_at=datetime(2026, 10, 20, 8, tzinfo=UTC),
            )
        )
        session.commit()
    t = bank_client.get("/insights/old-1/trace").json()
    assert t["run_id"] is None
    assert t["steps"] == []
    assert t["used_profile_fields"] is None
    assert t["prompt_version"] is None
    assert t["generated_by"] == "rules"


def test_trace_survives_run_deletion(news, modes, bank_client, db):
    modes("fake", "db")
    result = run_for_company(DRINKS, "cfo", "week")
    with db.begin() as conn:
        conn.execute(text("DELETE FROM pipeline_runs"))
    t = bank_client.get(f"/insights/{result.insights[0].id}/trace").json()
    assert t["run_id"] is None


def test_trace_not_found(db_client):
    r = db_client.get("/insights/nope/trace")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_trace_on_mocks(client, insight_id):
    r = client.get(f"/insights/{insight_id}/trace")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["run_id"] is None and t["steps"] == []
    assert t["sources"]
