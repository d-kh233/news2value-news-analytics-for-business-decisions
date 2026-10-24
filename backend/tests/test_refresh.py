"""refresh компаний из БД (services/refresh.py): сохранение инсайтов, pipeline_runs, режимы.

ML_MODE=fake|real и NEWS_SOURCE=mock|db задаются фикстурой modes; агент — в LLM_MODE=stub,
AGENT_TODAY=2026-10-22 (conftest), окно week — 15–22 октября.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.ingestion.normalizer import content_hash
from app.ml_ports import agent_port
from app.repositories import document_repo, insight_repo, pipeline_run_repo, profile_repo
from app.schemas.insight import Insight
from app.services.refresh import document_ids, run_for_company

DRINKS = "U_2"  # Торговля оптовая напитками, ВЭД: Китай, Турция
SOFTWARE = "U_4"  # Разработка программного обеспечения
BLOCKS = {"what_happened", "why_it_matters", "consequences", "actions", "bank_view"}


@pytest.fixture
def modes(monkeypatch):
    """Переключить ML_MODE и NEWS_SOURCE (настройки кешируются — сбрасываем кеш)."""

    def set_modes(ml_mode: str, news_source: str) -> None:
        monkeypatch.setenv("ML_MODE", ml_mode)
        monkeypatch.setenv("NEWS_SOURCE", news_source)
        get_settings.cache_clear()

    yield set_modes
    get_settings.cache_clear()


def _add_news(db: Engine, n: int, title: str, text: str, day: int) -> int:
    values = {
        "kind": "external_news",
        "source_name": "test_feed",
        "url": f"https://news.example.com/{n}",
        "title": title,
        "text": text,
        "published_at": datetime(2026, 10, day, 9, tzinfo=UTC),
        "tags": ["economy"],
        "status": "ok",
    }
    values["content_hash"] = content_hash(text + values["url"])
    with Session(db) as session:
        doc_id = document_repo.insert_new(session, values)
        session.commit()
    assert doc_id is not None
    return doc_id


@pytest.fixture
def news(db: Engine, bank_client) -> dict[str, int]:
    """Компании из tests/fixtures/*.csv и три неразмеченные новости в documents."""
    return {
        "drinks": _add_news(
            db,
            1,
            "Оптовые цены на напитки выросли",
            "Торговля напитками дорожает: поставки из Китая подорожали на 12%.",
            21,
        ),
        "software": _add_news(
            db,
            2,
            "Разработчикам программного обеспечения продлили льготы",
            "Компании, которые ведут разработку программного обеспечения, сохранят льготу.",
            20,
        ),
        "rate": _add_news(
            db, 3, "Банк России сохранил ключевую ставку", "Ставка осталась на уровне 16%.", 22
        ),
    }


def _saved(company_id: str):
    from app.core.db import get_session

    with get_session() as session:
        rows = insight_repo.list_for_company(session, company_id)
        session.expunge_all()
    return rows


def _run_row(run_id: int):
    from app.core.db import get_session

    with get_session() as session:
        run = pipeline_run_repo.get(session, run_id)
        session.expunge_all()
    return run


def _snapshot(company_id: str) -> dict[str, dict]:
    return {r.id: r.payload for r in _saved(company_id)}


# ---------- ML_MODE=fake ----------


def test_fake_refresh_saves_insights(news, modes):
    modes("fake", "db")
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "done", result.error
    rows = _saved(DRINKS)
    assert [r.document_ids for r in rows] == [[news["drinks"]]]
    row = rows[0]
    assert row.generated_by == "fake" and row.role == "cfo" and row.type == "event"
    # payload — ровно карточка API; в схеме Insight нет значения fake, поэтому там rules
    card = Insight.model_validate(row.payload)
    assert card.generated_by == "rules" and card.details is None
    assert card.sources[0].id == f"N:doc-{news['drinks']}"
    run = _run_row(result.run_id)
    assert run.status == "done" and run.error is None and run.finished_at is not None
    assert [s["name"] for s in run.steps] == ["Поиск новостей", "Совпадение слов с профилем"]


def test_fake_result_differs_by_industry(news, modes):
    modes("fake", "db")
    drinks = run_for_company(DRINKS, "ceo", "week")
    software = run_for_company(SOFTWARE, "ceo", "week")
    assert drinks.status == software.status == "done"
    assert {i for r in _saved(DRINKS) for i in r.document_ids} == {news["drinks"]}
    assert {i for r in _saved(SOFTWARE) for i in r.document_ids} == {news["software"]}


def test_fake_uses_profile_manual_data(news, modes):
    """Сырьё из ручной части профиля тоже ищется в новостях."""
    from app.core.db import get_session

    with get_session() as session:
        profile_repo.save_manual(session, SOFTWARE, {"raw_materials": ["ставка"]})
        session.commit()
    modes("fake", "db")
    run_for_company(SOFTWARE, "ceo", "week")
    assert {i for r in _saved(SOFTWARE) for i in r.document_ids} == {
        news["software"],
        news["rate"],
    }


def test_fake_with_mock_news_does_not_link_documents(news, modes):
    """ML_MODE и NEWS_SOURCE независимы: fake на моковых новостях работает, документов нет."""
    modes("fake", "mock")
    result = run_for_company(DRINKS, "ceo", "week")
    assert result.status in ("done", "empty"), result.error
    assert all(r.document_ids == [] for r in _saved(DRINKS))


# ---------- повторный refresh и сбои ----------


def test_repeat_refresh_replaces_not_duplicates(news, modes):
    modes("fake", "db")
    run_for_company(DRINKS, "cfo", "week")
    first = _snapshot(DRINKS)
    run_for_company(DRINKS, "cfo", "week")
    assert set(_snapshot(DRINKS)) == set(first)
    run_for_company(DRINKS, "accountant", "week")
    ids = set(_snapshot(DRINKS))
    assert ids and all(i.endswith("-accountant") for i in ids)
    # у другой компании инсайты не тронуты
    assert _saved(SOFTWARE) == []


def test_agent_failure_keeps_old_insights(news, modes, monkeypatch):
    modes("fake", "db")
    run_for_company(DRINKS, "cfo", "week")
    before = _snapshot(DRINKS)

    def boom(*args, **kwargs):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(agent_port, "generate_insights", boom)
    modes("real", "db")
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "failed" and "модель недоступна" in result.error
    assert _snapshot(DRINKS) == before
    run = _run_row(result.run_id)
    assert run.status == "failed" and "RuntimeError: модель недоступна" in run.error


def test_broken_card_keeps_old_insights(news, modes, monkeypatch):
    """Карточка не проходит схему Insight: запуск failed, старые инсайты и трейс на месте."""
    modes("fake", "db")
    run_for_company(DRINKS, "cfo", "week")
    before = _snapshot(DRINKS)
    trace = [{"step": 1, "name": "Поиск новостей", "status": "ok"}]
    monkeypatch.setattr(
        agent_port,
        "generate_insights",
        lambda *a, **k: ([{"id": "x", "company_id": DRINKS}], {"trace": trace}),
    )
    modes("real", "db")
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "failed" and "ValidationError" in result.error
    assert _snapshot(DRINKS) == before
    assert _run_row(result.run_id).steps == trace


def test_unknown_company_not_found(news, modes):
    modes("fake", "db")
    with pytest.raises(NotFoundError):
        run_for_company("U_404", "cfo", "week")
    from app.core.db import get_session

    with get_session() as session:
        assert pipeline_run_repo.list_for_company(session, "U_404") == []


# ---------- ML_MODE=real (агент в LLM_MODE=stub) ----------


def test_real_agent_on_db_news_without_labels_is_empty_not_silent(news, modes):
    """Неразмеченные новости из documents: прогон не падает, карточек 0, причина в pipeline_runs."""
    modes("fake", "db")
    run_for_company(DRINKS, "cfo", "week")
    before = _snapshot(DRINKS)
    modes("real", "db")
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "empty" and result.insights == []
    assert "NEWS_SOURCE=db" in result.error and "неразмеченные новости" in result.error
    assert _snapshot(DRINKS) == before  # лента не опустела
    run = _run_row(result.run_id)
    assert run.status == "empty" and run.error == result.error
    marking = next(s for s in run.steps if s["name"] == "Разметка новостей")
    assert marking["status"] == "skipped" and "LLM_MODE=stub" in marking["note"]
    search = next(s for s in run.steps if s["name"] == "Поиск новостей")
    assert search["output"]["source"] == "provider" and search["output"]["n"] == 3


def test_real_agent_on_mock_news_saves_cards(news, modes):
    modes("real", "mock")
    result = run_for_company(DRINKS, "cfo", "week")
    assert result.status == "done", result.error
    rows = _saved(DRINKS)
    assert rows and len(rows) == len({r.id for r in rows})
    for row in rows:
        card = Insight.model_validate(row.payload)
        assert row.generated_by in ("llm", "rules") and row.generated_by == card.generated_by
        assert BLOCKS <= set(card.details) and card.details["agent"]["run_id"]
        assert row.prompt_version == card.prompt_version and row.role == "cfo"
        assert row.document_ids == []  # моковые новости — не строки documents
    run = _run_row(result.run_id)
    assert run.status == "done" and len(run.steps) > 3


# ---------- agent_port: период и профиль из БД ----------


def test_period_to_days():
    assert agent_port.period_to_days("day") == 1
    assert agent_port.period_to_days("week") == 7
    assert agent_port.period_to_days(None) == 30
    with pytest.raises(ValueError):
        agent_port.period_to_days("month")


def test_generate_insights_passes_period_days(news, monkeypatch):
    agent_port._ensure_repo_on_path()
    import agent.orchestrator as orchestrator

    seen = {}
    original = orchestrator.Orchestrator.run

    def spy(self, company, period_days=30, top_k=None, role=None):
        seen["period_days"] = period_days
        return original(self, company, period_days, top_k, role)

    monkeypatch.setattr(orchestrator.Orchestrator, "run", spy)
    agent_port.generate_insights(DRINKS, "cfo", period="day")
    assert seen["period_days"] == 1
    agent_port.generate_insights(DRINKS, "cfo")
    assert seen["period_days"] == 30


def test_row_from_db_includes_manual_data(news):
    from app.core.db import get_session

    with get_session() as session:
        profile_repo.save_manual(session, DRINKS, {"raw_materials": ["стеклотара", "сахар"]})
        session.commit()
    row = agent_port.row_from_db(DRINKS)
    assert row["manual_data"]["raw_materials"] == ["стеклотара", "сахар"]
    from agent.schemas import CompanyProfile

    profile = CompanyProfile.from_row(row, DRINKS)
    assert profile.row["RAW_MATERIALS"] == ["стеклотара", "сахар"]
    assert profile.row["MAINOKVED_CODE"] == "46.34.2"  # банковские поля на месте


def test_document_ids_from_card():
    card = {
        "details": {"news_id": "N:doc-7", "what_happened": [{"source_ids": ["N:doc-7", "F:X"]}]},
        "sources": [{"id": "N:doc-12"}, {"id": "N:M01"}, {"id": "A:BA-1:0"}],
    }
    assert document_ids(card) == [7, 12]
