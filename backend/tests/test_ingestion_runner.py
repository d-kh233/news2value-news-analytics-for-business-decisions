"""Запуск сбора с БД: дубли, повторный запуск, упавший источник (без интернета)."""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.ingestion.runner import (
    PdfSource,
    RssSource,
    SourcesConfig,
    format_report,
    load_sources,
    run_ingestion,
)
from tests.conftest import INGESTION, NEWS_SITE

REPO_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
def pdf_dir(tmp_path) -> Path:
    folder = tmp_path / "analytics"
    shutil.copytree(INGESTION / "pdf", folder)
    return folder


def _rows(db: Engine) -> list[dict]:
    with db.connect() as conn:
        result = conn.execute(text("SELECT * FROM documents ORDER BY id"))
        return [dict(r._mapping) for r in result]


def _by_name(report):
    return {s.name: s for s in report.sources}


def _rss(*sources: RssSource) -> SourcesConfig:
    return SourcesConfig(rss=list(sources))


FEED_SOURCE = RssSource(name="test_feed", url=f"{NEWS_SITE}/rss", tags=["economy"])


# ---------- PDF ----------


def test_pdf_ingest_and_rerun(db, pdf_dir):
    config = SourcesConfig(pdf=[PdfSource(name="alfa_analytics", dir=pdf_dir)])

    first = _by_name(run_ingestion(db, config))["alfa_analytics"]
    assert (first.found, first.new, first.empty_text, first.errors) == (2, 2, 1, 0)
    rows = _rows(db)
    assert {r["kind"] for r in rows} == {"bank_analytics"}
    assert {r["url"]: r["status"] for r in rows} == {
        "local://analytics/key_rate_outlook.pdf": "ok",
        "local://analytics/scan_no_text.pdf": "empty_text",
    }

    second = _by_name(run_ingestion(db, config))["alfa_analytics"]
    assert (second.new, second.updated, second.dup_url) == (0, 0, 2)
    assert len(_rows(db)) == 2


def test_same_pdf_under_other_name_is_hash_duplicate(db, pdf_dir):
    config = SourcesConfig(pdf=[PdfSource(name="alfa_analytics", dir=pdf_dir)])
    run_ingestion(db, config)
    shutil.copy(pdf_dir / "key_rate_outlook.pdf", pdf_dir / "key_rate_outlook_copy.pdf")

    report = _by_name(run_ingestion(db, config))["alfa_analytics"]
    assert (report.new, report.dup_url, report.dup_hash) == (0, 2, 1)
    assert len(_rows(db)) == 2


def test_missing_pdf_dir_does_not_stop_rss(db, tmp_path, fake_web):
    config = SourcesConfig(
        pdf=[PdfSource(name="nowhere", dir=tmp_path / "missing")], rss=[FEED_SOURCE]
    )
    with fake_web.client() as client:
        report = _by_name(run_ingestion(db, config, client=client))
    assert report["nowhere"].failed
    assert report["test_feed"].failed is None
    assert report["test_feed"].new == 3


# ---------- RSS ----------


def test_rss_ingest(db, fake_web):
    with fake_web.client() as client:
        report = _by_name(run_ingestion(db, _rss(FEED_SOURCE), client=client))["test_feed"]
    assert (report.found, report.new, report.errors) == (3, 3, 0)
    rows = {r["url"].rsplit("/", 1)[-1]: r for r in _rows(db)}
    assert {r["kind"] for r in rows.values()} == {"external_news"}
    assert "Совет директоров" in rows["rate-decision"]["text"]
    assert rows["rate-decision"]["tags"] == ["economy", "Экономика"]
    assert "text_from_summary" in rows["ruble"]["tags"]
    assert rows["rate-decision"]["published_at"].isoformat() == "2026-10-20T07:15:00+00:00"


def test_broken_feed_does_not_stop_others(db, fake_web):
    fake_web.routes["https://down.example.com/robots.txt"] = (404, b"")
    fake_web.routes["https://down.example.com/rss"] = "down"
    fake_web.routes["https://bad.example.com/rss"] = (200, b"<html><p>not a feed")
    broken = RssSource(name="broken", url="https://down.example.com/rss")
    not_feed = RssSource(name="not_feed", url="https://bad.example.com/rss")

    with fake_web.client() as client:
        report = run_ingestion(db, _rss(broken, not_feed, FEED_SOURCE), client=client)
    by_name = _by_name(report)
    assert "ConnectError" in by_name["broken"].failed
    assert "FeedError" in by_name["not_feed"].failed
    assert by_name["test_feed"].new == 3
    assert len(_rows(db)) == 3
    text_report = format_report(report)
    assert "ИСТОЧНИК УПАЛ" in text_report
    assert "упало: 2" in text_report


def test_rss_rerun_creates_nothing_and_downloads_no_articles(db, fake_web):
    with fake_web.client() as client:
        run_ingestion(db, _rss(FEED_SOURCE), client=client)
    fake_web.requests.clear()

    with fake_web.client() as client:
        report = _by_name(run_ingestion(db, _rss(FEED_SOURCE), client=client))["test_feed"]
    assert (report.new, report.dup_url) == (0, 3)
    assert len(_rows(db)) == 3
    # повторно скачаны только robots.txt и лента, статьи — нет
    assert fake_web.requests == [f"{NEWS_SITE}/robots.txt", f"{NEWS_SITE}/rss"]


def test_same_article_in_two_feeds_is_hash_duplicate(db, fake_web):
    mirror_site = "https://mirror.example.com"
    feed = (INGESTION / "feed.xml").read_text(encoding="utf-8").replace(NEWS_SITE, mirror_site)
    fake_web.routes[f"{mirror_site}/robots.txt"] = (404, b"")
    fake_web.routes[f"{mirror_site}/rss"] = (200, feed.encode("utf-8"))
    fake_web.routes[f"{mirror_site}/economy/rate-decision"] = fake_web.routes[
        f"{NEWS_SITE}/economy/rate-decision"
    ]
    mirror = RssSource(name="mirror", url=f"{mirror_site}/rss")

    with fake_web.client() as client:
        report = _by_name(run_ingestion(db, _rss(FEED_SOURCE, mirror), client=client))
    # те же тексты по другим адресам (статья и описания из ленты) — не сохраняются
    assert (report["mirror"].new, report["mirror"].dup_hash) == (0, 3)
    assert len(_rows(db)) == 3


def test_failed_article_is_retried_and_updated_in_place(db, fake_web):
    article = f"{NEWS_SITE}/economy/rate-decision"
    saved = fake_web.routes[article]
    feed = (
        (INGESTION / "feed.xml")
        .read_bytes()
        .replace("Регулятор оставил ставку без изменений.".encode(), b"")
    )
    fake_web.routes[f"{NEWS_SITE}/rss"] = (200, feed)
    fake_web.routes[article] = (500, b"")

    with fake_web.client() as client:
        first = _by_name(run_ingestion(db, _rss(FEED_SOURCE), client=client))["test_feed"]
    assert first.errors == 1
    status = {r["url"]: r["status"] for r in _rows(db)}
    assert status[article] == "error"

    fake_web.routes[article] = saved
    with fake_web.client() as client:
        second = _by_name(run_ingestion(db, _rss(FEED_SOURCE), client=client))["test_feed"]
    assert (second.new, second.updated, second.dup_url) == (0, 1, 2)
    rows = _rows(db)
    assert len(rows) == 3
    fixed = next(r for r in rows if r["url"] == article)
    assert fixed["status"] == "ok" and "Совет директоров" in fixed["text"]


# ---------- sources.yaml ----------


def test_load_sources_resolves_pdf_dir_from_repo_root(tmp_path):
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "sources.yaml"
    path.write_text(
        "delay_s: 2\n"
        "pdf:\n  - {name: alfa, dir: data/raw/analytics}\n"
        "rss:\n  - {name: feed, url: 'https://x.example/rss', tz_offset: '+05:00'}\n",
        encoding="utf-8",
    )
    config = load_sources(path)
    assert config.delay_s == 2
    assert config.pdf[0].dir == tmp_path / "data" / "raw" / "analytics"
    assert config.rss[0].max_items == 20
    assert config.rss[0].tz.utcoffset(None).total_seconds() == 5 * 3600


def test_load_sources_rejects_bad_offset(tmp_path):
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "sources.yaml"
    path.write_text("rss:\n  - {name: f, url: 'https://x/rss', tz_offset: MSK}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_sources(path)


def test_repo_sources_yaml_is_valid():
    config = load_sources(REPO_DIR / "config" / "sources.yaml")
    assert config.rss and config.pdf
    assert config.pdf[0].dir == REPO_DIR / "data" / "raw" / "analytics"
    assert len({s.name for s in [*config.pdf, *config.rss]}) == len(config.pdf) + len(config.rss)
