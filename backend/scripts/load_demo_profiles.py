"""Загрузка демо-профилей из data/demo_profiles/*.json в ручную часть профилей (manual_data).

Запуск из backend/ (после load_csv.py — компании должны уже быть в БД):
    uv run python scripts/load_demo_profiles.py
Внутри контейнера api:
    docker compose exec api python scripts/load_demo_profiles.py

Формат файлов — в app/services/demo_profiles.py. Повторный запуск даёт тот же результат.
Код выхода 1, если какой-то файл пропущен.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import outside_docker_url  # noqa: E402
from app.services.demo_profiles import format_report, load_dir  # noqa: E402

# backend/scripts -> корень репозитория; в контейнере /app/scripts -> / (там /data/demo_profiles)
DEMO_DIR = Path(__file__).resolve().parents[2] / "data" / "demo_profiles"


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузка демо-профилей компаний")
    parser.add_argument("--dir", type=Path, default=DEMO_DIR)
    parser.add_argument("--database-url", help="по умолчанию DATABASE_URL из окружения / .env")
    args = parser.parse_args()

    url = args.database_url or get_settings().database_url
    if not url:
        print("Не задан адрес БД: DATABASE_URL или --database-url", file=sys.stderr)
        return 2
    if not args.dir.is_dir():
        print(f"Папка не найдена: {args.dir}", file=sys.stderr)
        return 2

    engine = create_engine(outside_docker_url(url))
    report = load_dir(engine, args.dir)
    engine.dispose()

    print(format_report(report))
    return 1 if report.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
