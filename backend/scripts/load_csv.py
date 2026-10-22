"""Загрузка CSV банка из data/raw/ в PostgreSQL и печать отчёта.

Запуск из backend/ (БД в Docker, скрипт на компьютере):
    uv run python scripts/load_csv.py
Адрес БД — DATABASE_URL из .env; хост db заменяется на 127.0.0.1:POSTGRES_HOST_PORT.
Внутри контейнера api:
    docker compose exec api python scripts/load_csv.py

Повторный запуск не создаёт дублей. Код выхода 1, если отчёт не сходится.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import outside_docker_url  # noqa: E402
from app.services.bank_loader import DEFAULT_CHUNK_SIZE, format_report, load_all  # noqa: E402

# backend/scripts -> корень репозитория; в контейнере /app/scripts -> / (там смонтирован /data/raw)
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузка CSV банка в БД")
    parser.add_argument("--companies", type=Path, default=RAW_DIR / "df_Uid_data_hack.csv")
    parser.add_argument("--links", type=Path, default=RAW_DIR / "df_Fid_Uid_link_hack.csv")
    parser.add_argument("--payments", type=Path, default=RAW_DIR / "df_Uid_payment_hack.csv")
    parser.add_argument("--database-url", help="по умолчанию DATABASE_URL из окружения / .env")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = parser.parse_args()

    url = args.database_url or get_settings().database_url
    if not url:
        print("Не задан адрес БД: DATABASE_URL или --database-url", file=sys.stderr)
        return 2
    for path in (args.companies, args.links, args.payments):
        if not path.is_file():
            print(f"Файл не найден: {path}", file=sys.stderr)
            return 2

    engine = create_engine(outside_docker_url(url))
    started = time.monotonic()
    report = load_all(engine, args.companies, args.links, args.payments, args.chunk_size)
    engine.dispose()

    print(format_report(report))
    print(f"\nВремя загрузки: {time.monotonic() - started:.1f} с")
    balanced = all(r.balanced for r in (report.companies, report.links, report.payments))
    return 0 if balanced else 1


if __name__ == "__main__":
    sys.exit(main())
