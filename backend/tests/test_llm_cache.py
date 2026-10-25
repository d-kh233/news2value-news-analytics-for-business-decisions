"""Кэш ответов (B6): таблица llm_cache и обёртка cached (services/llm_cache.py).

Сначала обёртка на тестовой функции, потом вместе с заглушкой ML_MODE=fake:
generate_insight (одна карточка на новость) и get_relevant_news (новости за период).
"""

import time

import pytest
from sqlalchemy import Engine, text

from app.ml_ports import fake_port
from app.services.llm_cache import cached
from app.services.refresh import run_for_company
from tests.conftest import add_news

DRINKS = "U_2"


def _cache_rows(db: Engine) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(text("SELECT key, value, hits FROM llm_cache ORDER BY created_at"))
        return [dict(r) for r in rows.mappings()]


@pytest.fixture
def square(db_client):
    """Функция под кэшем, которая считает свои настоящие вызовы."""
    state = {"version": "v1", "calls": 0, "context": "a"}

    def check(value: dict) -> None:
        if value["sq"] < 0:
            raise ValueError("отрицательный квадрат")

    @cached("square", version=lambda: state["version"], validate=check)
    def fn(x: int, *, negate: bool = False) -> dict:
        state["calls"] += 1
        return {"x": x, "sq": -x * x if negate else x * x}

    @cached("square_ctx", version=lambda: "v1", validate=check, context=lambda: state["context"])
    def with_context(x: int) -> dict:
        state["calls"] += 1
        return {"x": x, "sq": x * x}

    fn.state = state
    fn.with_context = with_context
    return fn


# ---------- обёртка ----------


def test_cache_off_by_default_calls_every_time(square, db):
    assert square(3) == square(3) == {"x": 3, "sq": 9}
    assert square.state["calls"] == 2
    assert _cache_rows(db) == []


def test_cache_hit_returns_saved_and_counts_hits(square, db, env):
    env(CACHE_ENABLED=1)
    assert square(3) == {"x": 3, "sq": 9}
    assert square(3) == {"x": 3, "sq": 9}
    assert square(3) == {"x": 3, "sq": 9}
    assert square.state["calls"] == 1
    [row] = _cache_rows(db)
    assert row["value"] == {"x": 3, "sq": 9} and row["hits"] == 2
    assert len(row["key"]) == 64  # sha256


def test_cache_key_depends_on_args(square, db, env):
    env(CACHE_ENABLED=1)
    square(3)
    square(4)
    square(4)
    square(3, negate=True)
    assert square.state["calls"] == 3
    assert len(_cache_rows(db)) == 2  # отрицательный квадрат не прошёл проверку


def test_prompt_version_change_resets_cache(square, db, env):
    env(CACHE_ENABLED=1)
    square(3)
    square.state["version"] = "v2"
    square(3)
    square(3)
    assert square.state["calls"] == 2
    assert [r["hits"] for r in _cache_rows(db)] == [0, 1]


def test_context_change_is_a_miss(square, env):
    env(CACHE_ENABLED=1)
    square.with_context(2)
    square.state["context"] = "b"
    square.with_context(2)
    assert square.state["calls"] == 2


def test_invalid_answer_is_not_cached(square, db, env):
    env(CACHE_ENABLED=1)
    assert square(3, negate=True) == {"x": 3, "sq": -9}  # ответ отдан как есть
    square(3, negate=True)
    assert square.state["calls"] == 2
    assert _cache_rows(db) == []


def test_cache_failure_falls_back_to_call(square, env, monkeypatch):
    """БД кэша недоступна — функция просто вызывается, генерация не ломается."""
    from app.core import db as db_module

    def no_db():
        raise RuntimeError("нет БД")

    env(CACHE_ENABLED=1)
    monkeypatch.setattr(db_module, "get_engine", no_db)
    assert square(5) == {"x": 5, "sq": 25}
    assert square.state["calls"] == 1


# ---------- кэш в конвейере (заглушка ML_MODE=fake) ----------


@pytest.fixture
def cards_made(news, env, monkeypatch):
    """Счётчик настоящих вызовов заглушки (тело generate_insight) при включённом кэше."""
    env(ML_MODE="fake", NEWS_SOURCE="db", CACHE_ENABLED=1, FAKE_DELAY_SECONDS=0.3)
    calls = {"n": 0}
    original = fake_port._card

    def spy(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(fake_port, "_card", spy)
    return calls


def test_second_run_does_not_call_stub(cards_made, db):
    t0 = time.perf_counter()
    first = run_for_company(DRINKS, "cfo", "week")
    first_s = time.perf_counter() - t0
    assert first.status == "done" and cards_made["n"] == 1
    assert first_s >= 0.3  # пауза FAKE_DELAY_SECONDS на карточку

    t0 = time.perf_counter()
    second = run_for_company(DRINKS, "cfo", "week")
    assert time.perf_counter() - t0 < 0.3
    assert second.status == "done" and cards_made["n"] == 1  # заглушка не вызывалась
    assert [i.model_dump() for i in second.insights] == [i.model_dump() for i in first.insights]
    hits = {r["hits"] for r in _cache_rows(db)}
    assert hits == {1}  # новости и карточка взяты из кэша по одному разу


def test_other_role_is_a_miss(cards_made):
    run_for_company(DRINKS, "cfo", "week")
    run_for_company(DRINKS, "ceo", "week")
    assert cards_made["n"] == 2


def test_prompt_version_change_regenerates(cards_made, monkeypatch, db):
    run_for_company(DRINKS, "cfo", "week")
    monkeypatch.setattr(fake_port, "PROMPT_VERSION", "fake@v2")
    result = run_for_company(DRINKS, "cfo", "week")
    assert cards_made["n"] == 2
    assert [i.prompt_version for i in result.insights] == ["fake@v2"]


def test_new_document_is_not_hidden_by_news_cache(cards_made, db):
    """В ключ новостей входит отпечаток documents: новая новость видна при следующем refresh."""
    first = run_for_company(DRINKS, "cfo", "week")
    doc_id = add_news(db, 9, "Импорт напитков из Турции вырос", "Поставки напитков выросли.", 22)
    second = run_for_company(DRINKS, "cfo", "week")
    assert len(second.insights) == len(first.insights) + 1
    assert doc_id in {i for ids in second.document_ids.values() for i in ids}
    assert cards_made["n"] == 2  # старая карточка из кэша, новая посчитана


def test_cache_disabled_pipeline_works_as_before(news, env, db):
    env(ML_MODE="fake", NEWS_SOURCE="db", FAKE_DELAY_SECONDS=0)
    assert run_for_company(DRINKS, "cfo", "week").status == "done"
    assert _cache_rows(db) == []
