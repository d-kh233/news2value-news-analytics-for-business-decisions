"""Окружение Alembic: откуда брать адрес БД и описание таблиц."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.core.db import outside_docker_url, to_sqlalchemy_url
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    # sqlalchemy.url задают тесты; иначе — DATABASE_URL из окружения / .env
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return to_sqlalchemy_url(url)
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL не задан")
    # вне Docker хост db подменяется на 127.0.0.1:POSTGRES_HOST_PORT
    return outside_docker_url(url)


def run_migrations_offline() -> None:
    """Режим --sql: печатает SQL без подключения к БД."""
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
