from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
