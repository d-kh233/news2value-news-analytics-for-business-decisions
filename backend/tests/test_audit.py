"""Журнал audit_log (B8+B9): каждый вызов ML-функции, даже неудачный; токены и дневной лимит."""

import logging

import pytest
from sqlalchemy import text

from app.core import logging as app_logging
from app.core.context import add_tokens
from app.ml_ports import agent_port, digest_port
from app.ml_ports.audit import TokenLimitExceeded, audited
from app.services import audit_service, job_service
from app.services.industry_tagging import tag_documents
from app.services.refresh import run_for_company

DRINKS = "U_2"


def rows(db) -> list[dict]:
    with db.connect() as conn:
        result = conn.execute(text("SELECT * FROM audit_log WHERE action = 'ml_call' ORDER BY id"))
        return [dict(r._mapping) for r in result]


def tools(db) -> list[str]:
    return [r["tool"] for r in rows(db)]


def test_fake_refresh_calls_are_logged(news, modes, db):
    modes("fake", "db")
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "done"
    logged = rows(db)
    by_tool = {r["tool"] for r in logged}
    assert {"fake.generate_insights", "news.get_relevant_news", "fake.generate_insight"} <= by_tool
    run = next(r for r in logged if r["tool"] == "fake.generate_insights")
    assert run["status"] == "ok" and run["company_id"] == DRINKS
    assert run["input_refs"] == {"company_id": DRINKS, "role": "cfo", "period": "week"}
    assert run["duration_ms"] is not None and run["user_id"] is None
    card = next(r for r in logged if r["tool"] == "fake.generate_insight")
    assert card["input_refs"]["news_id"] == f"doc-{news['drinks']}"


def test_real_refresh_is_logged(news, modes, db):
    modes("real", "db")
    run_for_company(DRINKS, "cfo", "week")
    assert "agent.generate_insights" in tools(db)
    assert "news.get_relevant_news" in tools(db)


def test_failed_call_is_logged(news, modes, db, monkeypatch):
    modes("real", "db")

    def broken(company_id):
        raise RuntimeError("агент сломан")

    monkeypatch.setattr(agent_port, "row_from_db", broken)
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "failed"
    failed = [r for r in rows(db) if r["tool"] == "agent.generate_insights"]
    assert failed[-1]["status"] == "error"
    assert "агент сломан" in failed[-1]["error"]


def test_no_texts_in_journal(news, modes, db):
    modes("fake", "db")
    run_for_company(DRINKS, "cfo", "week")
    dump = str(rows(db))
    assert "Торговля напитками дорожает" not in dump  # текст новости
    assert "Оптовые цены на напитки" not in dump  # заголовок


def test_tagging_is_logged(news, db):
    tag_documents(db)
    tagged = [r for r in rows(db) if r["tool"] == "llm.okved.industry_tags"]
    assert len(tagged) == 3
    assert {r["input_refs"]["document"] for r in tagged} == {f"doc-{i}" for i in news.values()}


def test_worker_writes_user_of_the_job(news, modes, db):
    modes("fake", "db")
    job_service.request_refresh(DRINKS, None, "42")
    job = job_service.claim_next()
    assert job_service.execute(job) == "done"
    assert {r["user_id"] for r in rows(db)} == {"42"}


def test_tokens_and_cache_hit(db, db_client):
    @audited("test.tool", lambda x: ("U_2", {"x": x}))
    def call(x):
        add_tokens(40)
        add_tokens(2)
        return x

    call(1)
    row = rows(db)[-1]
    assert row["tokens_used"] == 42 and row["status"] == "ok"
    assert audit_service.tokens_today() == 42


def test_journal_failure_does_not_break_call(monkeypatch, db_client):
    from app.repositories import audit_repo

    def boom(*_a, **_k):
        raise RuntimeError("нет БД")

    monkeypatch.setattr(audit_repo, "add", boom)

    @audited("test.tool")
    def call():
        return "ответ"

    assert call() == "ответ"


# ---- дневной лимит токенов ----


def spend(tokens: int) -> None:
    audit_service.record("ml_call", "ok", tool="test.spent", tokens_used=tokens)


def test_limit_fails_refresh_with_clear_error(news, modes, env, db):
    modes("real", "db")
    env(LLM_DAILY_TOKEN_LIMIT=500)
    spend(600)
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "failed"
    assert "Дневной лимит токенов" in result.error
    assert rows(db)[-1]["status"] == "limit"


def test_limit_job_failed_worker_alive(news, modes, env, db):
    modes("real", "db")
    env(LLM_DAILY_TOKEN_LIMIT=500)
    spend(600)
    job_id = job_service.request_refresh(DRINKS, None)
    assert job_service.execute(job_service.claim_next()) == "failed"
    from app.core.auth import DEMO_USER

    job = job_service.get_job(job_id, DEMO_USER)
    assert job.status == "failed" and "Дневной лимит токенов" in job.error


def test_limit_not_reached_and_zero_means_no_limit(news, modes, env, db):
    modes("real", "db")
    spend(600)
    env(LLM_DAILY_TOKEN_LIMIT=0)
    assert run_for_company(DRINKS, "cfo", "week").status != "failed"
    env(LLM_DAILY_TOKEN_LIMIT=10_000)
    assert run_for_company(DRINKS, "cfo", "week").status != "failed"


def test_limit_does_not_touch_fake_mode(news, modes, env, db):
    modes("fake", "db")
    env(LLM_DAILY_TOKEN_LIMIT=500)
    spend(600)
    assert run_for_company(DRINKS, "cfo", "week").status == "done"


def test_limit_digest_falls_back(news, modes, env, db):
    modes("real", "db")
    env(LLM_DAILY_TOKEN_LIMIT=500)
    spend(600)
    result = digest_port.digest_summary(DRINKS, "week", [])
    assert result["generated_by"] == "fallback"


def test_limit_exception_type():
    assert issubclass(TokenLimitExceeded, RuntimeError)


# ---- маскирование секретов в логах ----


@pytest.mark.parametrize(
    "raw, hidden",
    [
        ("вход: неудача (d.kh.53880@gmail.com)", "d.kh.53880@gmail.com"),
        ("Authorization: Bearer abc.def-123", "abc.def-123"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_part-1", "eyJhbGciOiJIUzI1NiJ9"),
        ("ключ sk-proj-1234567890abcdef", "sk-proj-1234567890abcdef"),
        ("password=hunter22 и api_key: 'qwerty123'", "hunter22"),
        ("postgresql://news2value:change_me@db:5432/x", "change_me"),
    ],
)
def test_mask_patterns(raw, hidden):
    masked = app_logging.mask_secrets(raw)
    assert hidden not in masked
    assert "***" in masked


def test_mask_keeps_ordinary_text():
    text_ = "refresh U_2: 3 карточки, tokens_used=120, max_tokens=1500"
    assert app_logging.mask_secrets(text_) == text_


def test_formatter_masks_configured_secret_and_traceback(env):
    env(JWT_SECRET="super-secret-value-123", LLM_API_KEY="key-from-env-456")
    app_logging._secret_values.cache_clear()
    try:
        fmt = app_logging.MaskingFormatter(app_logging.FORMAT)
        try:
            raise ValueError("ключ key-from-env-456 не подошёл")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "t", logging.ERROR, __file__, 1, "секрет %s", ("super-secret-value-123",),
                sys.exc_info(),
            )  # fmt: skip
        out = fmt.format(record)
        assert "super-secret-value-123" not in out
        assert "key-from-env-456" not in out
        assert "Traceback" in out
    finally:
        app_logging._secret_values.cache_clear()


def test_formatter_cuts_long_messages():
    fmt = app_logging.MaskingFormatter(app_logging.FORMAT)
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "%s", ("я" * 10_000,), None)
    out = fmt.format(record)
    assert len(out) < app_logging.MESSAGE_MAX_LEN + 200
    assert "обрезано" in out
