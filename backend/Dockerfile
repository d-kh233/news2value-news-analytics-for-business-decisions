FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

# Контекст сборки — корень репозитория (docker-compose.yml: build.context: .),
# чтобы в образ попали agent/ и llm/ — ML-часть, которую вызывает app/ml_ports
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev

COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/scripts ./scripts
COPY llm ./llm
COPY agent ./agent
# записанные ответы моделей: с LLM_MODE=replay API работает без ключа (режим для жюри)
COPY replay_cache ./replay_cache
# данные из git для запуска с чистого клона (B8+B9): синтетика и демо-профили для seed_demo.py,
# моки для отката USE_MOCKS=true, список источников для ingest.py. Пути — те же, что монтирует
# docker-compose.yml (/data/..., /config); data/raw в образ не попадает
COPY data/synthetic /data/synthetic
COPY data/demo_profiles /data/demo_profiles
COPY data/mocks /data/mocks
COPY config /config

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# при старте: сначала миграции БД (alembic upgrade head), потом API
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
