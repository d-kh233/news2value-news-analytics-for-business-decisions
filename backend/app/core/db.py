"""Подключение к PostgreSQL через SQLAlchemy."""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def to_sqlalchemy_url(url: str) -> str:
    """postgresql://... -> postgresql+psycopg://... (указываем драйвер psycopg 3)."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def outside_docker_url(url: str) -> str:
    """Адрес БД для запуска на компьютере, а не в контейнере.

    Хост db — имя сервиса в docker-compose, снаружи Docker его нет: заменяем на
    127.0.0.1 и порт POSTGRES_HOST_PORT. Внутри контейнера адрес не меняется.
    Именно 127.0.0.1, а не localhost: в Windows localhost сначала пробует IPv6 (::1),
    а порт опубликован только на IPv4 — каждое подключение ждало бы таймаута.
    """
    parsed = make_url(to_sqlalchemy_url(url))
    if parsed.host != "db" or Path("/.dockerenv").exists():
        return parsed.render_as_string(hide_password=False)
    port = get_settings().postgres_host_port
    return parsed.set(host="127.0.0.1", port=port).render_as_string(hide_password=False)


@lru_cache
def get_engine() -> Engine:
    """Engine (пул соединений с БД) по DATABASE_URL; создаётся при первом вызове."""
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL не задан")
    return create_engine(to_sqlalchemy_url(url), pool_pre_ping=True)


def get_session() -> Session:
    """Новая сессия (единица работы с БД); закрывать через `with`."""
    return sessionmaker(bind=get_engine())()
