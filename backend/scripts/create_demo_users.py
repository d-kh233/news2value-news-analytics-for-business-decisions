"""Создать тестовых пользователей разных ролей и компаний (B8+B9).

Запуск из backend/ (после загрузки компаний: load_csv.py или seed_demo.py):
    uv run python scripts/create_demo_users.py
Внутри контейнера api:
    docker compose exec api python scripts/create_demo_users.py

Пароль — DEMO_USER_PASSWORD из .env, вход (POST /auth/login) подписывается JWT_SECRET.
Повторный запуск обновляет пароли, дублей нет.
Код выхода: 0 — все созданы, 1 — кто-то пропущен (нет компании), 2 — не задан пароль / нет БД.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.services.demo_users import create_demo_users, demo_password, format_report  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    setup_logging(get_settings().log_level)
    try:
        password = demo_password()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        report = create_demo_users(password)
    except Exception as exc:  # noqa: BLE001 — понятное сообщение вместо трейсбека
        print(f"Не удалось записать пользователей: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(format_report(report))
    if not get_settings().jwt_secret:
        print("Внимание: JWT_SECRET не задан — вход (POST /auth/login) ответит 503")
    return 1 if report.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
