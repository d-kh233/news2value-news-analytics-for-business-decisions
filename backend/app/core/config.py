from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    app_name: str = "News2Value API"
    log_level: str = "INFO"
    database_url: str = ""
    # порт Postgres из Docker на компьютере (см. docker-compose.yml)
    postgres_host_port: int = 5432
    # Адреса фронтенда, которым разрешён доступ к API (в .env — JSON-список)
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    # Откуда агент берёт новости: mock — agent/data/mock_news.json, db — таблица documents (B4)
    news_source: Literal["mock", "db"] = "mock"
    # Кто делает инсайты при refresh: real — агент (agent_port), fake — заглушка на совпадении
    # слов (fake_port), если agent/llm не слиты или сломаны. Режим LLM внутри агента — LLM_MODE
    ml_mode: Literal["fake", "real"] = "real"
    # «Сегодня» агента (YYYY-MM-DD); пусто — текущая дата. Им же пользуется news_provider
    agent_today: str = ""
    # Кэш ответов generate_insight / get_relevant_news в таблице llm_cache (1 — включён)
    cache_enabled: bool = False
    # Пауза заглушки (ML_MODE=fake) на каждую карточку, секунды — имитация ответа модели
    fake_delay_seconds: float = 0.0
    # Задача refresh в running дольше этого — worker, вероятно, упал: помечаем failed
    job_timeout_minutes: float = 10.0
    # Как часто worker проверяет таблицу jobs, секунды
    worker_poll_seconds: float = 1.0
    # true — инсайты, дайджест, ask и feedback из моков data/mocks (как до B7): запасной вариант
    # демо. По умолчанию false — всё из БД. Компании, профили и refresh всегда из БД
    use_mocks: bool = False
    # Авторизация (B8+B9). Пока входа нет: false — все запросы от пользователя demo (админ),
    # true — 401 (заготовка app/core/auth.py)
    auth_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
