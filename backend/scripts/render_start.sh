#!/bin/sh
# Запуск API на бесплатном Render (render.yaml, dockerCommand: sh scripts/render_start.sh).
# На бесплатном тарифе нет отдельного worker и консоли, поэтому в одном контейнере:
#   1) миграции базы;
#   2) в фоне: демо-данные (seed_demo, повторный запуск дублей не создаёт), затем worker;
#   3) API — сразу, чтобы Render увидел открытый порт.
# Отдельным файлом, а не строкой в render.yaml: Render не разбирает кавычки в dockerCommand.
set -e
alembic upgrade head
(
  python scripts/seed_demo.py --source synthetic || echo "seed_demo завершился с ошибкой — подробности выше"
  exec python -m app.worker
) &
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
