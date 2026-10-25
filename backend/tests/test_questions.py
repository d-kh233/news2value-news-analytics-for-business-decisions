"""Вопросы по инсайту на БД (B7b): ask -> задача -> worker -> ответ в истории и в /result."""

import json

import pytest
from sqlalchemy import text

from app.core.auth import User, current_user
from app.main import app
from app.ml_ports import agent_port, llm_call, qa_port
from app.services import job_service
from tests.test_insights_db import add_insight

INSIGHT = "U_6-doc-1-ceo"
SOURCES = [
    {"id": "N:doc-1", "title": "ЦБ сохранил ставку", "url": "https://e.org/1",
     "published_at": "2026-10-21T09:00:00Z", "source_kind": "external"},
    {"id": "A:BA-01:0", "title": "Тезис банка", "url": "",
     "published_at": "2026-10-01T00:00:00Z", "source_kind": "bank_analytics"},
]  # fmt: skip


@pytest.fixture
def card(db, bank_client):
    add_insight(db, INSIGHT, "risk", 80, 22, [], sources=SOURCES)
    return bank_client


@pytest.fixture
def as_user():
    """Подменить текущего пользователя (как будет при включённой авторизации B8+B9)."""

    def set_user(user: User) -> None:
        app.dependency_overrides[current_user] = lambda: user

    yield set_user
    app.dependency_overrides.pop(current_user, None)


def _ask(client, question: str = "Что делать с кредитом?", insight: str = INSIGHT):
    return client.post(f"/insights/{insight}/ask", json={"question": question})


def _run_worker_once() -> str:
    job = job_service.claim_next()
    assert job is not None
    return job_service.execute(job)


def test_ask_pending_running_done(card, monkeypatch):
    r = _ask(card)
    assert r.status_code == 202
    job_id, question_id = r.json()["job_id"], r.json()["question_id"]
    assert set(r.json()) == {"job_id", "question_id"}
    assert card.get(f"/jobs/{job_id}").json() == {"id": job_id, "status": "pending", "error": None}
    assert card.get(f"/jobs/{job_id}/result").status_code == 404  # ещё не готов
    history = card.get(f"/insights/{INSIGHT}/questions").json()
    assert history["items"][0]["status"] == "pending" and history["items"][0]["answer"] is None

    seen = []
    real = qa_port.answer_question

    def spy(insight, question, hist):
        q = card.get(f"/insights/{INSIGHT}/questions").json()["items"][0]
        seen.append((q["status"], card.get(f"/jobs/{job_id}").json()["status"]))
        return real(insight, question, hist)

    monkeypatch.setattr(qa_port, "answer_question", spy)
    assert _run_worker_once() == "done"
    assert seen == [("running", "running")]

    job = card.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done" and job["error"] is None
    assert set(job) == {"id", "status", "error"}  # схема Job не расширена
    result = card.get(f"/jobs/{job_id}/result").json()
    assert "Что делать с кредитом?" in result["answer"]  # LLM_MODE=stub — ответ-шаблон
    assert [s["title"] for s in result["sources"]] == ["ЦБ сохранил ставку", "Тезис банка"]
    q = card.get(f"/insights/{INSIGHT}/questions").json()["items"][0]
    assert (q["id"], q["status"], q["answer"]) == (question_id, "done", result["answer"])
    assert q["sources"] == result["sources"]


def test_history_kept_and_passed_to_model(card, monkeypatch, env, tmp_path):
    """ML_MODE=real: ответ пишет модель (через шлюз), прошлые ответы идут ей в историю."""
    agent_port.ensure_repo_on_path()
    import llm.client as llm_client

    prompts = []

    def fake_model(model, messages, **params):
        prompts.append(messages[-1]["content"])
        statement = {"text": "ставка сохранена", "origin": "fact", "source_ids": ["N:doc-1"]}
        answer = {"answer": f"Ответ {len(prompts)}", "statements": [statement]}
        return json.dumps(answer, ensure_ascii=False)

    def gateway():
        llm_client.CACHE_DIR = tmp_path
        return fake_model, "live"

    monkeypatch.setattr(llm_call, "gateway", gateway)
    env(ML_MODE="real")
    _ask(card, "Первый вопрос?")
    _run_worker_once()
    second = _ask(card, "Второй вопрос?").json()
    _run_worker_once()
    assert "Вопрос: Первый вопрос?\nОтвет: Ответ 1" in prompts[1]
    result = card.get(f"/jobs/{second['job_id']}/result").json()
    assert result["answer"] == "Ответ 2"
    assert [s["id"] for s in result["sources"]] == ["N:doc-1"]  # только то, на что сослался ответ
    items = card.get(f"/insights/{INSIGHT}/questions").json()["items"]
    assert [i["question"] for i in items] == ["Первый вопрос?", "Второй вопрос?"]
    assert [i["answer"] for i in items] == ["Ответ 1", "Ответ 2"]


def test_question_validation_422(card):
    for body in ({"question": ""}, {"question": "   "}, {"question": "я" * 501}, {}):
        r = card.post(f"/insights/{INSIGHT}/ask", json=body)
        assert r.status_code == 422, body
        assert r.json()["error"] == "validation_error"
    assert _ask(card, "я" * 500).status_code == 202


def test_question_limit_409(card):
    for n in range(20):
        assert _ask(card, f"Вопрос {n}?").status_code == 202
    r = _ask(card, "Двадцать первый?")
    assert r.status_code == 409
    detail = f"Лимит 20 вопросов к инсайту {INSIGHT} исчерпан"
    assert r.json() == {"error": "conflict", "detail": detail}


def test_ask_404(card):
    r = _ask(card, insight="nope")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert card.get("/insights/nope/questions").status_code == 404


def test_foreign_insight_403(card, as_user):
    as_user(User(id="u1", company_ids=frozenset({"U_1"})))
    r = _ask(card)
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "detail": "Нет доступа к инсайтам этой компании"}
    assert card.get(f"/insights/{INSIGHT}/questions").status_code == 403
    as_user(User(id="u2", company_ids=frozenset({"U_6"})))
    assert _ask(card).status_code == 202


def test_auth_enabled_without_b8_is_401(card, env):
    env(AUTH_ENABLED="true")
    r = _ask(card)
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"


def test_history_is_per_user(card, as_user):
    as_user(User(id="u1"))
    _ask(card, "Вопрос u1?")
    as_user(User(id="u2"))
    _ask(card, "Вопрос u2?")
    items = card.get(f"/insights/{INSIGHT}/questions").json()["items"]
    assert [i["question"] for i in items] == ["Вопрос u2?"]


def test_ask_not_blocked_by_refresh(card):
    """Индекс «одна активная задача» — только у refresh: вопросы идут параллельно с ним."""
    refresh_job = job_service.request_refresh("U_6", None)
    a, b = _ask(card).json()["job_id"], _ask(card).json()["job_id"]
    assert len({refresh_job, a, b}) == 3
    assert card.get(f"/jobs/{refresh_job}/result").status_code == 404  # у refresh результата нет


def test_insight_removed_by_refresh_question_failed(card, db):
    job_id = _ask(card).json()["job_id"]
    with db.begin() as conn:
        conn.execute(text("DELETE FROM insights"))
    assert _run_worker_once() == "failed"
    job = card.get(f"/jobs/{job_id}").json()
    assert job["status"] == "failed" and "больше нет" in job["error"]
    with db.connect() as conn:
        assert conn.execute(text("SELECT status FROM insight_questions")).scalar() == "failed"


def test_stale_ask_job_fails_question(card, db, env):
    _ask(card)
    job = job_service.claim_next()
    env(JOB_TIMEOUT_MINUTES="0")
    assert job_service.fail_stale() == [job.id]
    with db.connect() as conn:
        assert conn.execute(text("SELECT status FROM insight_questions")).scalar() == "failed"
