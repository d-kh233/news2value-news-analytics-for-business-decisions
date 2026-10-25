"""Сбор документов (PDF-аналитика банка и RSS-новости) в таблицу documents и печать отчёта.

Запуск из backend/ (БД в Docker, скрипт на компьютере):
    uv run python scripts/ingest.py                      # все источники из config/sources.yaml
    uv run python scripts/ingest.py --pdf-dir            # только PDF (папки из sources.yaml)
    uv run python scripts/ingest.py --pdf-dir ../data/raw/analytics   # только PDF из этой папки
    uv run python scripts/ingest.py --rss                # только RSS
Внутри контейнера api:
    docker compose exec api python scripts/ingest.py

Повторный запуск не создаёт дублей. Упавший источник не останавливает остальные.
Код выхода: 0 — готово (даже если часть источников упала), 1 — упали все источники,
2 — ошибка запуска (нет адреса БД, файла настроек или папки).
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import outside_docker_url  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.ingestion.runner import (  # noqa: E402
    PdfSource,
    format_report,
    load_sources,
    run_ingestion,
)

# backend/scripts -> корень репозитория; в контейнере /app/scripts -> / (там смонтирован /config)
REPO_DIR = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Сбор документов в таблицу documents")
    parser.add_argument(
        "--sources", type=Path, default=REPO_DIR / "config" / "sources.yaml", help="sources.yaml"
    )
    parser.add_argument(
        "--pdf-dir",
        nargs="?",
        const="",
        metavar="DIR",
        help="собрать PDF; без значения — папки из sources.yaml, со значением — эту папку",
    )
    parser.add_argument("--rss", action="store_true", help="собрать RSS-ленты из sources.yaml")
    parser.add_argument("--database-url", help="по умолчанию DATABASE_URL из окружения / .env")
    args = parser.parse_args()

    # без флагов — всё; с флагами — только выбранное
    want_pdf = args.pdf_dir is not None or not args.rss
    want_rss = args.rss or args.pdf_dir is None

    settings = get_settings()
    # WARNING: в отчёт не мешаются служебные сообщения библиотек
    setup_logging("WARNING")
    # pypdf сам чинит битые ссылки внутри файла и пишет об этом десятки строк — не нужно
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    url = args.database_url or settings.database_url
    if not url:
        print("Не задан адрес БД: DATABASE_URL или --database-url", file=sys.stderr)
        return 2
    if not args.sources.is_file():
        print(f"Файл настроек не найден: {args.sources}", file=sys.stderr)
        return 2
    config = load_sources(args.sources)
    if args.pdf_dir:
        pdf_dir = Path(args.pdf_dir).resolve()
        if not pdf_dir.is_dir():
            print(f"Папка с PDF не найдена: {pdf_dir}", file=sys.stderr)
            return 2
        name = config.pdf[0].name if config.pdf else "bank_analytics"
        tags = config.pdf[0].tags if config.pdf else []
        config.pdf = [PdfSource(name=name, dir=pdf_dir, tags=tags)]

    engine = create_engine(outside_docker_url(url))
    started = time.monotonic()
    report = run_ingestion(engine, config, pdf=want_pdf, rss=want_rss)
    engine.dispose()

    print(format_report(report))
    print(f"\nВремя сбора: {time.monotonic() - started:.1f} с")
    all_failed = report.sources and len(report.failed_sources) == len(report.sources)
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
