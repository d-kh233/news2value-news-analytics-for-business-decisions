"""Дайджест из БД (B7): период, порядок, пустой дайджест, заглушка, модель через шлюз, кэш."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ml_ports import agent_port, digest_port, llm_call
from app.models import InsightRow
from tests.test_insights_db import make_payload

COMPANY = "U_6"


def add_recent(db, insight_id: str, kind: str, importance: int, hours_ago: float) -> None:
    created = datetime.now(UTC) - timedelta(hours=hours_ago)
    payload = make_payload(insight_id, COMPANY, kind, importance, 20)
    payload["created_at"] = created.strftime("%Y-%m-%dT%H:%M:%SZ")
    with Session(db) as session:
        session.add(InsightRow(id=insight_id, company_id=COMPANY, document_ids=[], payload=payload,
                               type=kind, importance=importance, generated_by="rules",
                               created_at=created))  # fmt: skip
        session.commit()


@pytest.fixture
def recent(db, bank_client):
    add_recent(db, "today-risk", "risk", 60, 2)
    add_recent(db, "today-top", "opportunity", 90, 5)
    add_recent(db, "three-days", "risk", 95, 72)
    add_recent(db, "old", "event", 99, 24 * 10)
    return bank_client


def _digest(client, period: str) -> dict:
    r = client.get(f"/companies/{COMPANY}/digest", params={"period": period})
    assert r.status_code == 200, r.text
    return r.json()


def test_day_and_week_fake(recent, modes):
    modes("fake", "db")
    day = _digest(recent, "day")
    assert day["insight_ids"] == ["today-top", "today-risk"]
    assert day["summary"].startswith("За сутки: 2 инсайт(ов), из них рисков — 1, возможностей — 1.")
    assert "Главное: Заголовок today-top; Заголовок today-risk." in day["summary"]
    week = _digest(recent, "week")
    assert week["insight_ids"] == ["three-days", "today-top", "today-risk"]  # 10 дней назад — нет
    assert week["company_id"] == COMPANY and week["period"] == "week"


def test_top_10_only(db, bank_client, modes):
    modes("fake", "db")
    for n in range(12):
        add_recent(db, f"i{n:02d}", "event", 50 + n, 1)
    ids = _digest(bank_client, "day")["insight_ids"]
    assert len(ids) == 10 and ids[0] == "i11" and "i00" not in ids


def test_empty_digest_does_not_call_function(bank_client, modes, monkeypatch):
    modes("fake", "db")
    monkeypatch.setattr(digest_port, "digest_summary", lambda *a: pytest.fail("вызов без инсайтов"))
    body = _digest(bank_client, "week")
    assert body == {"company_id": COMPANY, "period": "week",
                    "summary": "Новых инсайтов нет.", "insight_ids": []}  # fmt: skip


def test_digest_404_and_422(bank_client):
    assert bank_client.get("/companies/NOPE/digest", params={"period": "day"}).status_code == 404
    r = bank_client.get(f"/companies/{COMPANY}/digest", params={"period": "month"})
    assert r.status_code == 422 and r.json()["error"] == "validation_error"


def test_real_mode_stub_falls_back_and_is_not_cached(recent, env, db):
    """ML_MODE=real, LLM_MODE=stub: модель не вызывается — заглушка, в кэш не кладётся."""
    env(ML_MODE="real", CACHE_ENABLED="1")
    assert _digest(recent, "day")["summary"].startswith("За сутки: 2 инсайт(ов)")
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM llm_cache")).scalar() == 0


def test_real_mode_uses_generate_digest_and_cache(recent, env, db, monkeypatch, tmp_path):
    """Модель (через шлюз) пишет сводку; повторный запрос — из llm_cache, модель не зовётся."""
    agent_port.ensure_repo_on_path()
    import llm.client as llm_client

    calls = []

    def fake_model(model, messages, **params):
        calls.append(messages[-1]["content"])
        return json.dumps({
            "title": "Сутки: спрос растёт", "overview": "Главное — рост спроса на жильё.",
            "items": [{"news_id": "I:today-top", "line": "спрос", "impact_type": "opportunity",
                       "importance": "high"},
                      {"news_id": "I:today-risk", "line": "риск", "impact_type": "risk",
                       "importance": "medium"}]}, ensure_ascii=False)  # fmt: skip

    def gateway():
        llm_client.CACHE_DIR = tmp_path  # дисковый кэш call_json — во временной папке
        return fake_model, "live"

    monkeypatch.setattr(llm_call, "gateway", gateway)
    env(ML_MODE="real", CACHE_ENABLED="1")
    first = _digest(recent, "day")
    assert first["summary"] == "Сутки: спрос растёт. Главное — рост спроса на жильё."
    assert first["insight_ids"] == ["today-top", "today-risk"]
    assert "[I:today-top] возможность, важность 90" in calls[0]
    assert "[F:MAINOKVED_CODE]" in calls[0]  # профиль компании из БД
    second = _digest(recent, "day")
    assert second == first and len(calls) == 1
    with db.connect() as conn:
        assert conn.execute(text("SELECT sum(hits) FROM llm_cache")).scalar() == 1


def test_fake_cache_key_by_ids_and_importance(recent, env, db, monkeypatch):
    env(ML_MODE="fake", NEWS_SOURCE="db", CACHE_ENABLED="1")
    calls = []
    real = digest_port.fake_summary
    monkeypatch.setattr(digest_port, "fake_summary", lambda *a: calls.append(1) or real(*a))
    _digest(recent, "day")
    _digest(recent, "day")
    assert len(calls) == 1
    with Session(db) as session:  # поменялась важность — другой ключ, пересчёт
        session.get(InsightRow, "today-risk").importance = 61
        session.commit()
    _digest(recent, "day")
    assert len(calls) == 2
