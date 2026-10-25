"""Обновить инсайты одной компании из БД (services/refresh.py) и напечатать итог.

Запуск из backend/ (БД в Docker, скрипт на компьютере):
    uv run python scripts/run_pipeline.py --company U_10 --role cfo --period week
Внутри контейнера api:
    docker compose exec api python scripts/run_pipeline.py --company U_10 --role cfo

Режимы — переменные окружения (или .env), независимо друг от друга:
    ML_MODE=real|fake       кто делает инсайты: агент или заглушка на совпадении слов
    NEWS_SOURCE=mock|db     откуда новости: agent/data/mock_news.json или таблица documents
    LLM_MODE=stub|live|replay  режим модели внутри агента (бэкенд его только передаёт)
Пример в PowerShell:  $env:ML_MODE="fake"; uv run python scripts/run_pipeline.py --company U_10

Код выхода: 0 — инсайты сохранены (done) или карточек нет (empty), 1 — запуск упал (failed),
2 — ошибка запуска (нет адреса БД, нет такой компании).
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import NotFoundError  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.ml_ports.agent_port import PERIOD_DAYS, ROLES  # noqa: E402
from app.services.refresh import RefreshResult, run_for_company  # noqa: E402

STATUS_RU = {
    "done": "инсайты заменены",
    "empty": "карточек нет, старые инсайты остались",
    "failed": "ошибка, старые инсайты остались",
}


def print_report(company_id: str, result: RefreshResult) -> None:
    s = get_settings()
    print(f"Компания {company_id}, запуск pipeline_runs #{result.run_id}")
    print(
        f"Режимы: ML_MODE={s.ml_mode}, NEWS_SOURCE={s.news_source}, "
        f"LLM_MODE={os.environ.get('LLM_MODE', 'stub')}"
    )
    print(f"Статус: {result.status} — {STATUS_RU[result.status]}")
    if result.error:
        print(f"Пояснение: {result.error}")
    print(f"\nИнсайты ({len(result.insights)}):")
    for ins in result.insights:
        docs = result.document_ids.get(ins.id) or []
        doc_text = ", ".join(map(str, docs)) if docs else "нет (моковые новости)"
        print(f"  [{ins.importance:3}] {ins.type:11} {ins.title}")
        print(f"        id={ins.id}  роль={ins.role}  {ins.generated_by}  документы: {doc_text}")
    print(f"\nШаги ({len(result.steps)}):")
    for st in result.steps:
        note = f" — {st['note']}" if st.get("note") else ""
        print(f"  {st.get('step', '?'):>2}. [{st.get('status')}] {st.get('name')}{note}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Обновить инсайты компании (refresh)")
    parser.add_argument("--company", required=True, help="id компании из таблицы companies")
    parser.add_argument(
        "--role", choices=ROLES, default=None, help="роль читателя (по умолчанию ceo)"
    )
    parser.add_argument("--period", choices=list(PERIOD_DAYS), default="week", help="окно новостей")
    args = parser.parse_args()

    # кириллица в консоли Windows
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    setup_logging("WARNING")
    if not get_settings().database_url:
        print("Не задан адрес БД: DATABASE_URL", file=sys.stderr)
        return 2
    try:
        result = run_for_company(args.company, args.role, args.period)
    except NotFoundError as exc:
        print(exc.detail, file=sys.stderr)
        return 2
    print_report(args.company, result)
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
