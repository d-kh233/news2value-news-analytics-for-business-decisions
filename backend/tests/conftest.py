import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import db as db_module
from app.core.config import get_settings
from app.core.db import outside_docker_url, to_sqlalchemy_url
from app.ingestion.normalizer import content_hash
from app.ingestion.rss_parser import PoliteClient
from app.main import app
from app.repositories import document_repo
from app.services import mock_store
from app.services.bank_loader import load_all

BACKEND_DIR = Path(__file__).resolve().parents[1]
BANK_TABLES = ["companies", "persons", "person_company_links", "payments", "rejected_rows"]
DB_TABLES = [
    *BANK_TABLES,
    "company_profiles",
    "documents",
    "insights",
    "pipeline_runs",
    "jobs",
    "llm_cache",
    "insight_questions",
    "insight_feedback",
]
FIXTURES = Path(__file__).parent / "fixtures"
# моковая компания с моковыми инсайтами (data/mocks); компании API живут в БД
MOCK_COMPANY_ID = "c-001"


def _test_database_url() -> str | None:
    """Адрес тестовой БД: TEST_DATABASE_URL или DATABASE_URL с базой news2value_test."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return to_sqlalchemy_url(url)
    base = get_settings().database_url
    if not base:
        return None
    parsed = make_url(outside_docker_url(base))
    return parsed.set(database="news2value_test").render_as_string(hide_password=False)


def run_migrations(url: str) -> None:
    """Применить миграции Alembic (alembic upgrade head) к указанной БД."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    # % в пароле configparser воспринимает как подстановку, поэтому экранируем
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """Тестовая Postgres: база создаётся при необходимости, схема — с нуля через миграции.

    Если Postgres не запущена, тесты с БД пропускаются (видно в `pytest -rs`).
    """
    url = _test_database_url()
    if not url:
        pytest.skip("Нет DATABASE_URL / TEST_DATABASE_URL — тесты с БД пропущены")
    parsed = make_url(url)
    admin = create_engine(
        parsed.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": parsed.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    except OperationalError as exc:
        pytest.skip(f"Postgres недоступна ({parsed.host}:{parsed.port}): {exc.orig}")
    finally:
        admin.dispose()

    engine = create_engine(url)
    # Чистая схема, чтобы проверить, что миграция создаёт таблицы с нуля
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    run_migrations(url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine: Engine) -> Engine:
    """Пустые таблицы банка и профилей перед каждым тестом."""
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(DB_TABLES)} RESTART IDENTITY"))
    return db_engine


@pytest.fixture
def db_client(db: Engine, monkeypatch) -> TestClient:
    """Клиент API, который работает с тестовой БД (пустые таблицы)."""
    monkeypatch.setattr(db_module, "get_engine", lambda: db)
    return TestClient(app)


@pytest.fixture
def bank_client(db: Engine, db_client: TestClient) -> TestClient:
    """Клиент API с загруженными фикстурами банка (tests/fixtures/*.csv)."""
    load_all(db, FIXTURES / "companies.csv", FIXTURES / "links.csv", FIXTURES / "payments.csv")
    return db_client


@pytest.fixture(autouse=True)
def _agent_offline(monkeypatch, tmp_path):
    """Агент в тестах не ходит в модель: карточки по правилам, дата моковых новостей фиксирована."""
    monkeypatch.setenv("LLM_MODE", "stub")
    monkeypatch.setenv("AGENT_TODAY", "2026-10-22")
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))


@pytest.fixture(autouse=True)
def _reset_mocks():
    """Перед каждым тестом возвращаем моки в исходное состояние."""
    mock_store.reset()
    yield


@pytest.fixture
def env(monkeypatch):
    """Задать переменные окружения для Settings (настройки кешируются — сбрасываем кеш)."""

    def set_env(**values: object) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, str(value))
        get_settings.cache_clear()

    yield set_env
    get_settings.cache_clear()


@pytest.fixture
def modes(env):
    """Переключить ML_MODE и NEWS_SOURCE."""

    def set_modes(ml_mode: str, news_source: str) -> None:
        env(ML_MODE=ml_mode, NEWS_SOURCE=news_source)

    return set_modes


def add_news(db: Engine, n: int, title: str, text: str, day: int) -> int:
    """Новость (external_news, ok) за day октября 2026 в таблицу documents; вернуть её id."""
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
    """Компании из tests/fixtures/*.csv и три неразмеченные новости в documents.

    U_2 (напитки) совпадает с новостью drinks, U_4 (ПО) — с software, rate — ни с кем.
    """
    return {
        "drinks": add_news(
            db,
            1,
            "Оптовые цены на напитки выросли",
            "Торговля напитками дорожает: поставки из Китая подорожали на 12%.",
            21,
        ),
        "software": add_news(
            db,
            2,
            "Разработчикам программного обеспечения продлили льготы",
            "Компании, которые ведут разработку программного обеспечения, сохранят льготу.",
            20,
        ),
        "rate": add_news(
            db, 3, "Банк России сохранил ключевую ставку", "Ставка осталась на уровне 16%.", 22
        ),
    }


@pytest.fixture
def client(env) -> TestClient:
    """Клиент API на моках (USE_MOCKS=true): инсайты c-001…c-003, как до B7."""
    env(USE_MOCKS="true")
    return TestClient(app)


@pytest.fixture
def company_id() -> str:
    return MOCK_COMPANY_ID


@pytest.fixture
def insight_id(client, company_id) -> str:
    return client.get(f"/companies/{company_id}/insights").json()["items"][0]["id"]


INGESTION = FIXTURES / "ingestion"
NEWS_SITE = "https://news.example.com"


@dataclass
class FakeWeb:
    """Интернет для тестов сбора: адрес -> (код, тело) или "down" (нет связи), "timeout".

    Запоминает адреса запросов, User-Agent и паузы — без настоящей сети и ожидания.
    """

    routes: dict[str, tuple[int, bytes] | str] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    user_agents: list[str] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        self.user_agents.append(request.headers.get("user-agent", ""))
        route = self.routes.get(url)
        if route == "down":
            raise httpx.ConnectError("нет связи", request=request)
        if route == "timeout":
            raise httpx.ReadTimeout("таймаут", request=request)
        if route is None:
            return httpx.Response(404, content=b"not found")
        status, body = route
        return httpx.Response(status, content=body)

    def client(self, delay_s: float = 0.0, timeout_s: float = 10.0) -> PoliteClient:
        return PoliteClient(
            "News2ValueTest/1.0",
            timeout_s,
            delay_s,
            transport=httpx.MockTransport(self.handle),
            sleep=self.sleeps.append,
            # часы стоят: каждый следующий запрос к сайту ждёт полную паузу
            clock=lambda: 0.0,
        )


@pytest.fixture
def fake_web() -> FakeWeb:
    """Тестовый сайт news.example.com: robots.txt, лента и одна статья (остальное — 404)."""
    web = FakeWeb()
    web.routes[f"{NEWS_SITE}/robots.txt"] = (200, (INGESTION / "robots.txt").read_bytes())
    web.routes[f"{NEWS_SITE}/rss"] = (200, (INGESTION / "feed.xml").read_bytes())
    web.routes[f"{NEWS_SITE}/economy/rate-decision"] = (
        200,
        (INGESTION / "article.html").read_bytes(),
    )
    web.routes[f"{NEWS_SITE}/private/insider"] = (200, b"<html>secret</html>")
    return web
