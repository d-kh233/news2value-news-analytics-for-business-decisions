"""Парсеры PDF и RSS на фикстурах tests/fixtures/ingestion (без интернета и без БД)."""

import shutil
from datetime import UTC, datetime

import httpx
import pytest

from app.ingestion import pdf_parser, rss_parser
from app.ingestion.normalizer import normalize
from app.ingestion.rss_parser import Disallowed, FeedError
from tests.conftest import INGESTION, NEWS_SITE

PDF_DIR = INGESTION / "pdf"
FEED = f"{NEWS_SITE}/rss"


# ---------- PDF ----------


def test_parse_pdf_with_text():
    draft = pdf_parser.parse_pdf(PDF_DIR / "key_rate_outlook.pdf", "alfa_analytics", ["bank"])
    values = normalize(draft)
    assert values["kind"] == "bank_analytics"
    assert values["status"] == "ok"
    assert values["url"] == "local://pdf/key_rate_outlook.pdf"
    # заголовок из метаданных PDF
    assert values["title"] == "Bank Analytics: Key Rate Outlook"
    # в имени и тексте даты нет — CreationDate 2026-09-25 12:00 +03:00 -> UTC
    assert values["published_at"] == datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    # перенос «tar-get» по строкам склеен
    assert "above target." in values["text"]
    assert values["tags"] == ["bank"]


def test_parse_pdf_without_text_layer_is_empty_text():
    draft = pdf_parser.parse_pdf(PDF_DIR / "scan_no_text.pdf", "alfa_analytics")
    values = normalize(draft)
    assert values["status"] == "empty_text"
    assert values["text"] == ""
    assert values["title"] == "scan_no_text"


def test_parse_pdf_date_from_filename_wins_over_metadata(tmp_path):
    # у пересохранённого файла CreationDate — дата пересохранения, а дата выхода — в имени
    path = tmp_path / "Bond_Brief_14092026.pdf"
    shutil.copy(PDF_DIR / "key_rate_outlook.pdf", path)
    draft = pdf_parser.parse_pdf(path, "alfa_analytics")
    assert normalize(draft)["published_at"] == datetime(2026, 9, 14, tzinfo=UTC)


def test_parse_broken_pdf_is_error_without_exception(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf at all")
    assert normalize(pdf_parser.parse_pdf(path, "alfa_analytics"))["status"] == "error"


def test_pdf_title_falls_back_to_first_line(tmp_path):
    # если в метаданных нет /Title: короткие строки (номер страницы) пропускаются
    assert pdf_parser._first_line("\n  1\nИнфляция: основные выводы\nтекст") == (
        "Инфляция: основные выводы"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Обзор от 25 сентября 2026 г.", datetime(2026, 9, 25, tzinfo=UTC)),
        ("Дата публикации: 01.10.2026", datetime(2026, 10, 1, tzinfo=UTC)),
        # берём дату, которая встретилась раньше
        ("03.08.2026 — данные на 25 сентября 2026", datetime(2026, 8, 3, tzinfo=UTC)),
        ("Без даты", None),
        ("31.02.2026 — несуществующий день", None),
    ],
)
def test_date_from_text(text, expected):
    assert pdf_parser.date_from_text(text) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("2026-09-01_inflation.pdf", datetime(2026, 9, 1, tzinfo=UTC)),
        ("inflation_20260901.pdf", datetime(2026, 9, 1, tzinfo=UTC)),
        ("infl_25.08.2026.pdf", datetime(2026, 8, 25, tzinfo=UTC)),
        # имена настоящих файлов аналитики
        ("FI_Strategy_3Q26_22072026.pdf", datetime(2026, 7, 22, tzinfo=UTC)),
        ("Бонд_Бриф_14092026.pdf", datetime(2026, 9, 14, tzinfo=UTC)),
        ("ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf", datetime(2021, 4, 15, tzinfo=UTC)),
        ("ALFA-Macro_Insights-Policy_Rate_11Sep.pdf", None),
        ("Gold_digest_August26.pdf", None),
        ("report_12345678.pdf", None),
        ("inflation.pdf", None),
    ],
)
def test_date_from_filename(name, expected):
    assert pdf_parser.date_from_filename(name) == expected


def test_iter_pdf_files(tmp_path):
    shutil.copy(PDF_DIR / "key_rate_outlook.pdf", tmp_path / "B.PDF")
    shutil.copy(PDF_DIR / "key_rate_outlook.pdf", tmp_path / "a.pdf")
    (tmp_path / "notes.txt").write_text("не PDF", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    assert [p.name for p in pdf_parser.iter_pdf_files(tmp_path)] == ["B.PDF", "a.pdf"]


# ---------- RSS ----------


def test_read_feed(fake_web):
    with fake_web.client() as client:
        entries = rss_parser.read_feed(client, FEED, max_items=20)
    assert [e.url for e in entries] == [
        f"{NEWS_SITE}/economy/rate-decision",
        f"{NEWS_SITE}/economy/ruble",
        f"{NEWS_SITE}/private/insider",
    ]
    assert entries[0].tags == ["Экономика"]
    # в ленте указан User-Agent робота
    assert set(fake_web.user_agents) == {"News2ValueTest/1.0"}


def test_read_feed_max_items(fake_web):
    with fake_web.client() as client:
        assert len(rss_parser.read_feed(client, FEED, max_items=1)) == 1


def test_read_feed_not_a_feed(fake_web):
    fake_web.routes[FEED] = (200, (INGESTION / "article.html").read_bytes())
    with fake_web.client() as client, pytest.raises(FeedError):
        rss_parser.read_feed(client, FEED, max_items=20)


def test_read_feed_http_error(fake_web):
    fake_web.routes[FEED] = (503, b"")
    with fake_web.client() as client, pytest.raises(httpx.HTTPStatusError):
        rss_parser.read_feed(client, FEED, max_items=20)


def _entries(fake_web):
    with fake_web.client() as client:
        return {e.url.rsplit("/", 1)[-1]: e for e in rss_parser.read_feed(client, FEED, 20)}


def test_fetch_article_full_text(fake_web):
    entry = _entries(fake_web)["rate-decision"]
    with fake_web.client() as client:
        values = normalize(rss_parser.fetch_article(client, entry, "test_feed", ["economy"]))
    assert values["status"] == "ok"
    assert values["kind"] == "external_news"
    assert "Совет директоров Банка России" in values["text"]
    # меню, реклама и подвал страницы в текст не попали
    assert "Реклама" not in values["text"] and "Все права защищены" not in values["text"]
    assert values["tags"] == ["economy", "Экономика"]
    assert values["published_at"] == datetime(2026, 10, 20, 7, 15, tzinfo=UTC)


def test_fetch_article_404_uses_summary(fake_web):
    entry = _entries(fake_web)["ruble"]
    with fake_web.client() as client:
        values = normalize(rss_parser.fetch_article(client, entry, "test_feed"))
    assert values["status"] == "ok"
    assert values["text"] == "Рубль ослаб на 3% к юаню за неделю. Импортёры отмечают рост цен."
    assert rss_parser.SUMMARY_TAG in values["tags"]


def test_fetch_article_timeout_uses_summary(fake_web):
    fake_web.routes[f"{NEWS_SITE}/economy/rate-decision"] = "timeout"
    entry = _entries(fake_web)["rate-decision"]
    with fake_web.client() as client:
        values = normalize(rss_parser.fetch_article(client, entry, "test_feed"))
    assert values["text"] == "Регулятор оставил ставку без изменений."
    assert rss_parser.SUMMARY_TAG in values["tags"]


def test_fetch_article_respects_robots(fake_web):
    entry = _entries(fake_web)["insider"]
    fake_web.requests.clear()
    with fake_web.client() as client:
        values = normalize(rss_parser.fetch_article(client, entry, "test_feed"))
    # robots.txt запрещает /private/ — страницу не запрашивали, текст из описания в ленте
    assert f"{NEWS_SITE}/private/insider" not in fake_web.requests
    assert values["text"] == "Описание закрытого материала из ленты."


def test_fetch_article_without_text_and_summary_is_error(fake_web):
    entry = _entries(fake_web)["ruble"]
    entry.summary = ""
    with fake_web.client() as client:
        values = normalize(rss_parser.fetch_article(client, entry, "test_feed"))
    assert values["status"] == "error"


# ---------- вежливый клиент ----------


def test_client_pauses_between_requests_to_one_site(fake_web):
    with fake_web.client(delay_s=1.5) as client:
        client.get(FEED)
        client.get(f"{NEWS_SITE}/economy/rate-decision")
    # robots.txt — первый запрос к сайту, без паузы; дальше пауза перед каждым запросом
    assert fake_web.requests[0] == f"{NEWS_SITE}/robots.txt"
    assert fake_web.sleeps == [1.5, 1.5]


def test_client_uses_crawl_delay_from_robots(fake_web):
    fake_web.routes[f"{NEWS_SITE}/robots.txt"] = (200, b"User-agent: *\nCrawl-delay: 5\n")
    with fake_web.client(delay_s=1.0) as client:
        client.get(FEED)
    assert fake_web.sleeps == [5.0]


def test_client_robots_missing_allows_all(fake_web):
    del fake_web.routes[f"{NEWS_SITE}/robots.txt"]
    with fake_web.client() as client:
        assert client.allowed(f"{NEWS_SITE}/private/insider")


@pytest.mark.parametrize("robots", [(503, b""), "down"])
def test_client_robots_unavailable_disallows(fake_web, robots):
    fake_web.routes[f"{NEWS_SITE}/robots.txt"] = robots
    with fake_web.client() as client, pytest.raises(Disallowed, match="robots.txt недоступен"):
        client.get(FEED)
    assert FEED not in fake_web.requests


def test_client_timeout_is_set(fake_web):
    with fake_web.client(timeout_s=3) as client:
        assert client._http.timeout == httpx.Timeout(3)


# ---------- robots.txt (RFC 9309) ----------

ROBOTS = """
# комментарий
User-agent: *
Disallow: /?
Disallow: /*utm_
Disallow: /search
Allow: /search$
Disallow: /files/*.pdf$
Crawl-delay: 2

User-agent: News2ValueBot
User-agent: OtherBot
Disallow: /private/
Allow: /private/open/
"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        # «Disallow: /?» запрещает только адреса с запросом от корня, а не весь сайт
        ("/rss/section-economics.xml", True),
        ("/?page=2", False),
        ("/news/1?utm_source=tg", False),
        ("/search", True),
        ("/search?q=ставка", False),
        ("/files/report.pdf", False),
        ("/files/report.pdf?v=1", True),
        ("/robots.txt", True),
    ],
)
def test_robots_rules_for_star_group(path, allowed):
    rules = rss_parser.RobotsRules.parse(ROBOTS, "SomeCrawler/2.0")
    assert rules.allowed(f"{NEWS_SITE}{path}") is allowed
    assert rules.crawl_delay == 2


def test_robots_rules_own_group_replaces_star():
    rules = rss_parser.RobotsRules.parse(ROBOTS, "News2ValueBot/0.1 (+https://example.com)")
    assert not rules.allowed(f"{NEWS_SITE}/private/insider")
    # более длинное Allow главнее Disallow
    assert rules.allowed(f"{NEWS_SITE}/private/open/doc")
    # правила группы «*» к нашему роботу не относятся
    assert rules.allowed(f"{NEWS_SITE}/?page=2")
    assert rules.crawl_delay is None


def test_robots_rules_empty_disallow_allows_all():
    rules = rss_parser.RobotsRules.parse("User-agent: *\nDisallow:\n", "Bot")
    assert rules.allowed(f"{NEWS_SITE}/anything")
    assert not rss_parser.RobotsRules.deny_all().allowed(f"{NEWS_SITE}/")
