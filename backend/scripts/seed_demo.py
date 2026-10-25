"""Пустая база -> готова к демо одной командой.

Шаги по порядку (после каждого — время и итог):
  1. CSV банка (load_csv) — пропускается, если компании уже загружены; --force-csv — заново;
  2. демо-профили из data/demo_profiles/*.json;
  3. сбор документов (ingest): без сети RSS упадут — это предупреждение, а не остановка;
  4. разметка документов по отраслям (okved:NN);
  5. refresh демо-компаний синхронно, без worker.

Запуск из backend/:
    uv run python scripts/seed_demo.py
    uv run python scripts/seed_demo.py --companies U_67064 U_19717 --role cfo --skip-ingest
Внутри контейнера api:
    docker compose exec api python scripts/seed_demo.py

Новости для агента — из базы (--news-source db, по умолчанию): у таких карточек есть ссылки на
документы, по ним работают фильтр industry и источники карточки. --news-source env — как в .env.
--mode auto (по умолчанию): генератор по ML_MODE из .env; если у компании 0 карточек или сбой —
повтор на заглушке ML_MODE=fake + NEWS_SOURCE=db, в итоге видно, чем сделаны карточки.
--mode env — без подстраховки.
Код выхода: 0 — у всех демо-компаний есть инсайты, 1 — у кого-то нет, 2 — ошибка запуска.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import get_session, outside_docker_url  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.ingestion.runner import format_report as ingest_report  # noqa: E402
from app.ingestion.runner import load_sources, run_ingestion  # noqa: E402
from app.repositories import insight_repo  # noqa: E402
from app.services import company_service, refresh  # noqa: E402
from app.services.bank_loader import format_report as csv_report  # noqa: E402
from app.services.bank_loader import load_all  # noqa: E402
from app.services.demo_profiles import format_report as demo_report  # noqa: E402
from app.services.demo_profiles import load_dir  # noqa: E402
from app.services.industry_tagging import format_report as tag_report  # noqa: E402
from app.services.industry_tagging import tag_documents  # noqa: E402

# backend/scripts -> корень репозитория; в контейнере /app/scripts -> / (там /data и /config)
REPO_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_DIR / "data" / "raw"
DEMO_DIR = REPO_DIR / "data" / "demo_profiles"
SOURCES = REPO_DIR / "config" / "sources.yaml"


def step(title: str, work: Callable[[], str]) -> None:
    print(f"\n=== {title}")
    started = time.monotonic()
    print(work())
    print(f"--- {time.monotonic() - started:.1f} с")


def load_csv(engine: Any, force: bool) -> str:
    _, total = company_service.list_companies(1, 0)
    if total and not force:
        return f"Пропущено: компании уже загружены ({total}). Загрузить заново: --force-csv"
    paths = [RAW_DIR / n for n in ("df_Uid_data_hack.csv", "df_Fid_Uid_link_hack.csv",
                                   "df_Uid_payment_hack.csv")]  # fmt: skip
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise SystemExit(f"Нет файлов CSV: {', '.join(missing)}")
    return csv_report(load_all(engine, *paths))


def ingest(engine: Any) -> str:
    report = run_ingestion(engine, load_sources(SOURCES))
    text = ingest_report(report)
    failed = report.failed_sources
    if failed:
        text += (
            f"\nПРЕДУПРЕЖДЕНИЕ: не собраны источники {', '.join(s.name for s in failed)} "
            "(нет сети или сайт недоступен) — работаем с тем, что уже есть в базе"
        )
    return text


def _set_modes(ml_mode: str, news_source: str) -> None:
    os.environ["ML_MODE"], os.environ["NEWS_SOURCE"] = ml_mode, news_source
    get_settings.cache_clear()


def _count(company_id: str) -> int:
    with get_session() as session:
        return insight_repo.count_for_company(session, company_id)


def refresh_company(company_id: str, role: str | None, period: str, mode: str) -> tuple[bool, str]:
    s = get_settings()
    modes = f"ML_MODE={s.ml_mode}, NEWS_SOURCE={s.news_source}"
    result = refresh.run_for_company(company_id, role, period)
    line = f"{company_id}: {result.status} ({modes}), карточек {len(result.insights)}"
    if result.status != "done" and mode == "auto" and (s.ml_mode, s.news_source) != ("fake", "db"):
        line += f"\n    причина: {(result.error or '')[:300]}"
        before = (s.ml_mode, s.news_source)
        _set_modes("fake", "db")
        try:
            result = refresh.run_for_company(company_id, role, period)
        finally:
            _set_modes(*before)
        line += f"\n    повтор на заглушке (ML_MODE=fake, NEWS_SOURCE=db): {result.status}, "
        line += f"карточек {len(result.insights)}"
    if result.status == "failed":
        line += f"\n    ошибка: {(result.error or '')[:300]}"
    return _count(company_id) > 0, line


def demo_companies() -> list[str]:
    return sorted(p.stem for p in DEMO_DIR.glob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Подготовка базы к демо")
    parser.add_argument("--companies", nargs="+", help="по умолчанию — из data/demo_profiles")
    parser.add_argument("--role", default=None, help="роль читателя; по умолчанию ceo")
    parser.add_argument("--period", choices=["day", "week"], default="week")
    parser.add_argument("--mode", choices=["auto", "env"], default="auto")
    parser.add_argument("--news-source", choices=["db", "mock", "env"], default="db")
    parser.add_argument("--force-csv", action="store_true", help="загрузить CSV банка заново")
    parser.add_argument("--skip-ingest", action="store_true", help="не собирать документы")
    args = parser.parse_args()

    setup_logging("WARNING")
    if args.news_source != "env":
        os.environ["NEWS_SOURCE"] = args.news_source
        get_settings.cache_clear()
    url = get_settings().database_url
    if not url:
        print("Не задан адрес БД: DATABASE_URL", file=sys.stderr)
        return 2
    engine = create_engine(outside_docker_url(url))
    companies = args.companies or demo_companies()
    if not companies:
        print(f"Нет демо-компаний: пустая папка {DEMO_DIR} и не задан --companies", file=sys.stderr)
        return 2
    started = time.monotonic()

    step("1. CSV банка", lambda: load_csv(engine, args.force_csv))
    step("2. Демо-профили", lambda: demo_report(load_dir(engine, DEMO_DIR)))
    if args.skip_ingest:
        print("\n=== 3. Сбор документов — пропущен (--skip-ingest)")
    else:
        step("3. Сбор документов", lambda: ingest(engine))
    step("4. Разметка отраслей", lambda: tag_report(tag_documents(engine)))

    ready: dict[str, bool] = {}

    def run_all() -> str:
        lines = []
        for cid in companies:
            ok, line = refresh_company(cid, args.role, args.period, args.mode)
            ready[cid] = ok
            lines.append(line)
        return "\n".join(lines)

    step(f"5. Инсайты демо-компаний (period={args.period}, mode={args.mode})", run_all)
    engine.dispose()

    print(f"\nГотово за {time.monotonic() - started:.1f} с.")
    for cid, ok in ready.items():
        state = "инсайты есть" if ok else "ИНСАЙТОВ НЕТ"
        print(f"  {cid}: {state} — GET /companies/{cid}/insights")
    return 0 if all(ready.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
