"""Разметка собранных документов по отраслям (теги okved:NN) и печать отчёта.

Запуск из backend/:
    uv run python scripts/tag_documents.py
Внутри контейнера api:
    docker compose exec api python scripts/tag_documents.py

Новые документы размечаются при сборе (scripts/ingest.py); этот скрипт — для собранных раньше
и после правки словаря в llm/okved/tagging.py. Повторный запуск ничего не меняет.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import outside_docker_url  # noqa: E402
from app.services.industry_tagging import format_report, tag_documents  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Разметка документов по отраслям")
    parser.add_argument("--database-url", help="по умолчанию DATABASE_URL из окружения / .env")
    args = parser.parse_args()
    url = args.database_url or get_settings().database_url
    if not url:
        print("Не задан адрес БД: DATABASE_URL или --database-url", file=sys.stderr)
        return 2
    engine = create_engine(outside_docker_url(url))
    print(format_report(tag_documents(engine)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
