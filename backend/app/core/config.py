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
    # Адрес фронтенда на сервере (https://app.example.ru); добавляется к CORS_ORIGINS
    frontend_origin: str = ""
    # Откуда агент берёт новости: mock — agent/data/mock_news.json, db — таблица documents (B4)
    news_source: Literal["mock", "db"] = "mock"
    # Кто делает инсайты при refresh: real — агент (agent_port), fake — заглушка на совпадении
    # слов (fake_port), если agent/llm не слиты или сломаны. Режим LLM внутри агента — LLM_MODE
    ml_mode: Literal["fake", "real"] = "real"
    # «Сегодня» агента (YYYY-MM-DD); пусто — текущая дата. Им же пользуется news_provider
    agent_today: str = ""
    # NEWS_SOURCE=db: если новости собирались дольше стольких часов назад, refresh сначала
    # собирает RSS из config/sources.yaml. 0 — не собирать (новости только через ingest.py)
    auto_ingest_hours: float = 0.0
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
    # Авторизация (B8+B9): false — все запросы от пользователя demo (админ, все компании);
    # true — нужен токен из POST /auth/login (заголовок Authorization: Bearer <токен>)
    auth_enabled: bool = False
    # Секрет подписи токенов входа (длинная случайная строка); без него вход недоступен,
    # а при AUTH_ENABLED=true API не стартует
    jwt_secret: str = ""
    # Срок жизни токена входа, минуты
    jwt_expire_minutes: float = 720.0
    # Сколько токенов модели можно потратить за сутки (UTC) по журналу audit_log; 0 — без лимита.
    # При превышении задача refresh/ask получает failed с понятной ошибкой, дайджест — заглушку
    llm_daily_token_limit: int = 0
    # ML_MODE=real: сколько ждать ответа дайджеста и ответа на вопрос, секунды
    ml_timeout_seconds: float = 60.0
    # ML_MODE=real: сколько ждать прогона агента (несколько вызовов модели), секунды
    ml_agent_timeout_seconds: float = 300.0
    # ML_MODE=real: сколько раз повторить вызов ML-функции после ошибки или таймаута
    ml_retries: int = 1
    # Пароль тестовых пользователей (scripts/create_demo_users.py, seed_demo.py)
    demo_user_password: str = ""
    # Секреты, которые бэкенд сам не использует, но маскирует в логах (core/logging.py):
    # ключ модели читает агент, пароль базы — Postgres
    llm_api_key: str = ""
    postgres_password: str = ""

    def allowed_origins(self) -> list[str]:
        """CORS_ORIGINS плюс FRONTEND_ORIGIN (без завершающего /), без повторов."""
        origins = list(self.cors_origins)
        front = self.frontend_origin.strip().rstrip("/")
        if front and front not in origins:
            origins.append(front)
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
