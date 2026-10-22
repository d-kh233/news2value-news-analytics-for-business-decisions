import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.db import outside_docker_url, to_sqlalchemy_url
from app.main import app
from app.services import mock_store

BACKEND_DIR = Path(__file__).resolve().parents[1]
BANK_TABLES = ["companies", "persons", "person_company_links", "payments", "rejected_rows"]


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
    """Пустые таблицы банка перед каждым тестом."""
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(BANK_TABLES)} RESTART IDENTITY"))
    return db_engine


@pytest.fixture(autouse=True)
def _reset_mocks():
    """Перед каждым тестом возвращаем моки в исходное состояние."""
    mock_store.reset()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def company_id(client) -> str:
    return client.get("/companies").json()["items"][0]["id"]


@pytest.fixture
def insight_id(client, company_id) -> str:
    return client.get(f"/companies/{company_id}/insights").json()["items"][0]["id"]
