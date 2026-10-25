"""Адаптеры ML_MODE=real (ml_ports/real.py) и выбор по режиму (ml_ports/loader.py)."""

import time

import pytest
from sqlalchemy import text

from app.core.context import acting_user
from app.ml_ports import agent_port, digest_port, fake_port, loader, qa_port, real
from app.ml_ports.base import MLError
from app.services.refresh import run_for_company

DRINKS = "U_2"


def test_loader_by_mode(env):
    env(ML_MODE="fake")
    ports = loader.get_ports()
    assert ports.mode == "fake"
    assert ports.generate_insights is fake_port.generate_insights
    assert ports.answer_question is qa_port.answer_question
    env(ML_MODE="real")
    ports = loader.get_ports()
    assert ports.mode == "real"
    assert ports.generate_insights is real.generate_insights
    assert ports.digest_summary is real.digest_summary


def test_timeout_then_retry_then_mlerror(env, monkeypatch):
    env(ML_AGENT_TIMEOUT_SECONDS=0.2, ML_RETRIES=1)
    calls = []

    def slow(*_a, **_k):
        calls.append(1)
        time.sleep(1)

    monkeypatch.setattr(agent_port, "generate_insights", slow)
    t0 = time.perf_counter()
    with pytest.raises(MLError, match=r"agent\.generate_insights: TimeoutError: нет ответа за 0.2"):
        real.generate_insights(DRINKS, "cfo", period="week")
    assert len(calls) == 2
    assert time.perf_counter() - t0 < 1.0  # зависший поток не ждём


def test_one_retry_recovers(env, monkeypatch):
    env(ML_RETRIES=1)
    calls = []

    def flaky(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("сеть моргнула")
        return [], {"run_id": "r"}

    monkeypatch.setattr(agent_port, "generate_insights", flaky)
    assert real.generate_insights(DRINKS) == ([], {"run_id": "r"})
    assert len(calls) == 2


def test_no_retry_on_unknown_company(env, monkeypatch):
    calls = []

    def missing(*_a, **_k):
        calls.append(1)
        raise LookupError("Компания U_0 не найдена")

    monkeypatch.setattr(agent_port, "generate_insights", missing)
    with pytest.raises(MLError, match="LookupError: Компания U_0 не найдена"):
        real.generate_insights("U_0")
    assert len(calls) == 1


def test_refresh_failed_with_mlerror_and_each_attempt_audited(news, modes, db, monkeypatch):
    modes("real", "db")

    def broken(company_id):
        raise RuntimeError("агент сломан")

    monkeypatch.setattr(agent_port, "row_from_db", broken)
    with acting_user("7"):
        result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "failed"
    assert result.error.startswith("MLError: agent.generate_insights: RuntimeError: агент сломан")
    assert "попыток: 2" in result.error
    with db.connect() as conn:
        logged = conn.execute(
            text("SELECT status, user_id FROM audit_log WHERE tool = 'agent.generate_insights'")
        ).all()
    # обе попытки в журнале, пользователь виден и в потоке адаптера
    assert [tuple(r) for r in logged] == [("error", "7"), ("error", "7")]


def test_real_refresh_makes_not_fake_cards(news, modes):
    modes("real", "db")
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status in ("done", "empty")
    for ins in result.insights:
        assert ins.generated_by in ("llm", "rules")


def test_digest_mlerror_falls_back(feed_for_digest, modes, monkeypatch, env):
    client = feed_for_digest
    modes("real", "db")
    env(ML_TIMEOUT_SECONDS=0.1, ML_RETRIES=0)

    def slow(*_a, **_k):
        time.sleep(1)

    monkeypatch.setattr(digest_port, "digest_summary", slow)
    r = client.get(f"/companies/{DRINKS}/digest", params={"period": "week"})
    assert r.status_code == 200, r.text
    assert r.json()["summary"].startswith("За неделю")


@pytest.fixture
def feed_for_digest(news, modes, bank_client, monkeypatch):
    """У U_2 свежие инсайты от заглушки (created_at — сейчас), чтобы дайджест не был пустым."""
    modes("fake", "db")
    assert run_for_company(DRINKS, "cfo", "week").status == "done"
    return bank_client
