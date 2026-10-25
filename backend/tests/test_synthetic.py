"""Синтетические данные для сдачи (data/synthetic): генератор, загрузка, демо без интернета."""

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.ml_ports.news_port import agent_today
from app.services.bank_loader import load_all
from app.services.demo_profiles import load_dir
from app.services.refresh import run_for_company
from app.services.synthetic_news import load_news

REPO = Path(__file__).resolve().parents[2]
SYNTHETIC = REPO / "data" / "synthetic"
DEMO = ["U_67064", "U_19717", "U_16346"]
CSV = ("df_Uid_data_hack.csv", "df_Fid_Uid_link_hack.csv", "df_Uid_payment_hack.csv")


def _generator():
    path = Path(__file__).resolve().parents[1] / "scripts" / "make_synthetic.py"
    spec = importlib.util.spec_from_file_location("make_synthetic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_files_match_generator(tmp_path, monkeypatch):
    gen = _generator()
    monkeypatch.setattr("sys.argv", ["make_synthetic.py", "--out", str(tmp_path)])
    assert gen.main() == 0
    for name in (*CSV, "news.json"):
        assert (tmp_path / name).read_bytes() == (SYNTHETIC / name).read_bytes(), name


def test_news_cover_required_topics():
    import json

    news = json.loads((SYNTHETIC / "news.json").read_text(encoding="utf-8"))
    assert 10 <= len(news) <= 15
    topics = {t for n in news for t in n["tags"]}
    assert {"key_rate", "fx", "commodities", "regulation"} <= topics
    assert all(n["title"].startswith("[ДЕМО]") for n in news)


@pytest.fixture
def synthetic_db(db, db_client):
    load_all(db, *(SYNTHETIC / n for n in CSV))
    load_dir(db, REPO / "data" / "demo_profiles")
    return db


def test_load_synthetic_csv(synthetic_db):
    with synthetic_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM companies")).scalar() == 30
        ids = {r[0] for r in conn.execute(text("SELECT synthetic_u FROM companies"))}
        assert set(DEMO) <= ids
        assert conn.execute(text("SELECT count(*) FROM payments")).scalar() > 50


def test_news_load_is_idempotent_and_fresh(synthetic_db):
    first = load_news(synthetic_db, SYNTHETIC / "news.json")
    again = load_news(synthetic_db, SYNTHETIC / "news.json")
    assert (first.new, again.new, again.updated) == (13, 0, 13)
    with synthetic_db.connect() as conn:
        dates = [r[0] for r in conn.execute(text("SELECT published_at FROM documents"))]
    assert all(agent_today() - timedelta(days=7) <= d.date() <= agent_today() for d in dates)


@pytest.mark.parametrize("ml_mode", ["fake", "real"])
def test_demo_companies_get_insights_offline(synthetic_db, modes, ml_mode):
    load_news(synthetic_db, SYNTHETIC / "news.json")
    modes(ml_mode, "db")
    for company_id in DEMO:
        result = run_for_company(company_id, "cfo", "week")
        assert result.status == "done", (company_id, result.error)
        assert result.insights
        if ml_mode == "real":
            assert all(i.generated_by in ("llm", "rules") for i in result.insights)
