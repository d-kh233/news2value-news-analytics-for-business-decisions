"""Проверка стыка бэкенда с агентом: настоящий agent_port.generate_insights на компаниях из БД.

Запуск из backend/:
    uv run python scripts/check_ml_contract.py                      # 2 компании разных отраслей
    uv run python scripts/check_ml_contract.py --companies U_10 U_2
    ML_MODE=real uv run python scripts/check_ml_contract.py         # плюс проверка real-адаптеров

Что проверяется (при NEWS_SOURCE=mock и NEWS_SOURCE=db по очереди):
  • сигнатура generate_insights — те параметры, которые вызывают refresh.py и mock_store;
  • ответ — пара (список карточек, сводка); каждая карточка проходит схему Insight,
    в сводке есть run_id, role, llm_mode, plan, trace, у шагов трейса — step, name, status;
  • при NEWS_SOURCE=db все ссылки N:doc-<id> ведут на строки таблицы documents;
  • сколько новостей агент нашёл и сколько из них смог разметить (0 карточек — предупреждение,
    а не нарушение контракта: так бывает на неразмеченных новостях в LLM_MODE=stub/replay).
При ML_MODE=real дополнительно (B8+B9) — адаптеры ml_ports/real.py, которые отдаёт loader:
  • loader.get_ports() отдаёт функции real.py, их сигнатуры совпадают с интерфейсами base.py;
  • прогон агента через адаптер проходит те же проверки, что и прямой;
  • дайджест и ответ на вопрос через адаптер возвращают нужные поля;
  • сбой агента превращается в MLError (с повтором), и каждая попытка есть в audit_log.
В базу инсайтов ничего не пишется; в audit_log проверка оставляет записи своих вызовов.

Код выхода: 0 — контракт соблюдён, 1 — есть нарушения, 2 — ошибка запуска (нет БД, компаний).
"""

import argparse
import inspect
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.db import get_session  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.ml_ports import agent_port, base, loader, real  # noqa: E402
from app.models import AuditRow  # noqa: E402
from app.repositories import company_repo, document_repo, insight_repo  # noqa: E402
from app.schemas.insight import Insight  # noqa: E402
from app.services.refresh import document_ids  # noqa: E402

# имя параметра -> вид: так generate_insights вызывают services/refresh.py и mock_store.py
EXPECTED_PARAMS = {
    "company_id": inspect.Parameter.POSITIONAL_OR_KEYWORD,
    "role": inspect.Parameter.POSITIONAL_OR_KEYWORD,
    "company": inspect.Parameter.KEYWORD_ONLY,
    "profile": inspect.Parameter.KEYWORD_ONLY,
    "period_days": inspect.Parameter.KEYWORD_ONLY,
    "period": inspect.Parameter.KEYWORD_ONLY,
}
SUMMARY_KEYS = {"run_id", "role", "llm_mode", "plan", "trace"}
STEP_KEYS = {"step", "name", "status"}


def check_signature() -> list[str]:
    params = inspect.signature(agent_port.generate_insights).parameters
    problems = []
    for name, kind in EXPECTED_PARAMS.items():
        if name not in params:
            problems.append(f"нет параметра {name}")
        elif params[name].kind != kind:
            problems.append(
                f"параметр {name}: {params[name].kind.description}, ждём {kind.description}"
            )
    extra = [p for p in params.values() if p.name not in EXPECTED_PARAMS and p.default is p.empty]
    problems += [f"новый обязательный параметр {p.name}" for p in extra]
    return problems


def _trace_step(trace: list[dict], name: str) -> dict:
    return next((st for st in trace if st.get("name") == name), {})


def check_run(
    company_id: str, role: str, period: str, news_source: str, generate: Any = None
) -> tuple[list[str], str]:
    """Один прогон агента: (нарушения контракта, строка итога для печати).

    generate — чем запускать (по умолчанию agent_port.generate_insights, для real — адаптер).
    """
    os.environ["NEWS_SOURCE"] = news_source
    get_settings.cache_clear()
    generate = generate or agent_port.generate_insights
    try:
        result = generate(company_id, role, period=period)
    except Exception as exc:  # noqa: BLE001 — падение агента — это тоже итог проверки
        return [f"агент упал: {type(exc).__name__}: {exc}"], "—"
    problems: list[str] = []
    if not (isinstance(result, tuple) and len(result) == 2):
        return [f"ответ не пара (cards, summary): {type(result).__name__}"], "—"
    cards, summary = result
    if missing := SUMMARY_KEYS - set(summary):
        problems.append(f"в сводке нет ключей {sorted(missing)}")
    trace = summary.get("trace") or []
    for st in trace:
        if missing := STEP_KEYS - set(st):
            problems.append(f"у шага трейса нет {sorted(missing)}: {st.get('name')}")
            break
    refs: set[int] = set()
    for card in cards:
        try:
            Insight.model_validate(card)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"карточка {card.get('id')} не проходит схему Insight: {exc}")
        refs |= set(document_ids(card))
    if news_source == "db" and refs:
        with get_session() as session:
            missing_docs = sorted(refs - document_repo.existing_ids(session, sorted(refs)))
        if missing_docs:
            problems.append(f"ссылки на несуществующие документы: {missing_docs}")
    found = (_trace_step(trace, "Поиск новостей").get("output") or {}).get("n", "?")
    marking = _trace_step(trace, "Разметка новостей")
    if marking:
        out = marking.get("output") or {}
        labeled = f"размечено {len(out.get('extracted', []))}" if out else marking.get("note")
    else:
        labeled = "разметка не нужна (новости уже размечены)"
    line = (
        f"новостей {found}, {labeled}; карточек {len(cards)}, ссылок на документы {len(refs)}; "
        f"LLM_MODE={summary.get('llm_mode')}"
    )
    if not cards:
        line += "  ВНИМАНИЕ: 0 карточек — лента при refresh не обновится (status=empty)"
    return problems, line


def _params(fn: Any) -> list[tuple[str, Any]]:
    sig = inspect.signature(fn)
    return [(p.name, p.kind) for p in sig.parameters.values() if p.name != "self"]


def _audit_count(tool: str, status: str) -> int:
    from sqlalchemy import func, select

    with get_session() as session:
        stmt = select(func.count()).where(AuditRow.tool == tool, AuditRow.status == status)
        return session.scalar(stmt) or 0


def check_real(company_id: str, role: str, period: str) -> list[str]:
    """Проверки ML_MODE=real: loader, сигнатуры base.py, адаптеры, MLError и журнал."""
    problems: list[str] = []
    ports = loader.get_ports()
    if ports.mode != "real":
        return [f"loader.get_ports() при ML_MODE=real отдал режим {ports.mode}"]
    pairs = [
        ("generate_insights", ports.generate_insights, base.InsightGenerator.__call__),
        ("digest_summary", ports.digest_summary, base.DigestWriter.__call__),
        ("answer_question", ports.answer_question, base.QuestionAnswerer.__call__),
    ]
    for name, fn, proto in pairs:
        if _params(fn) != _params(proto):
            problems.append(f"{name}{inspect.signature(fn)} не совпадает с base.py")
    print("  loader и сигнатуры base.py: " + ("ок" if not problems else "НАРУШЕНИЯ"))

    found, line = check_run(company_id, role, period, "db", generate=ports.generate_insights)
    print(f"  агент через адаптер: {'ок' if not found else 'НАРУШЕНИЯ'} — {line}")
    problems += [f"адаптер агента: {p}" for p in found]

    # дайджест — по сохранённым инсайтам компании (llm.digest на пустом списке бросает ValueError,
    # поэтому API пустой дайджест модели не отдаёт)
    with get_session() as session:
        items = [[r.id, r.importance] for r in insight_repo.list_for_company(session, company_id)]
    if not items:
        print("  дайджест через адаптер: пропущен — у компании нет сохранённых инсайтов (refresh)")
    else:
        try:
            digest = ports.digest_summary(company_id, period, items[:10])
            if not {"summary", "generated_by"} <= set(digest):
                problems.append(f"дайджест без полей summary/generated_by: {sorted(digest)}")
            print(f"  дайджест через адаптер: generated_by={digest.get('generated_by')}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"дайджест упал: {type(exc).__name__}: {exc}")
    insight = {
        "id": "check-ml-contract", "company_id": company_id, "title": "Проверка стыка",
        "summary": "Проверочная карточка", "type": "event", "importance": 50,
        "impact_on_company": "—", "recommended_action": "Ничего не делать", "sources": [],
        "created_at": "2026-10-25T00:00:00Z",
    }  # fmt: skip
    try:
        answer = ports.answer_question(insight, "Что делать?", [])
        if not {"answer", "sources", "generated_by"} <= set(answer):
            problems.append(f"ответ без полей answer/sources/generated_by: {sorted(answer)}")
        print(f"  ответ на вопрос через адаптер: generated_by={answer.get('generated_by')}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"ответ на вопрос упал: {type(exc).__name__}: {exc}")

    # сбой агента: адаптер должен повторить и отдать MLError, обе попытки — в журнале
    tool = "agent.generate_insights"
    before = _audit_count(tool, "error")
    original = agent_port.row_from_db

    def broken(_company_id: str) -> None:
        raise RuntimeError("проверочный сбой check_ml_contract")

    agent_port.row_from_db = broken  # type: ignore[assignment]
    try:
        real.generate_insights(company_id, role, period=period)
        problems.append("сбой агента не превратился в MLError")
    except base.MLError as exc:
        print(f"  сбой агента -> MLError: {exc}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"сбой агента дал {type(exc).__name__}, а не MLError")
    finally:
        agent_port.row_from_db = original  # type: ignore[assignment]
    attempts = 1 + max(0, get_settings().ml_retries)
    logged = _audit_count(tool, "error") - before
    if logged != attempts:
        problems.append(f"в audit_log {logged} записей о сбое, ждали {attempts} (по попытке)")
    else:
        print(f"  audit_log: обе попытки записаны ({logged})")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка стыка с agent_port на компаниях из БД")
    parser.add_argument(
        "--companies", nargs="+", help="id компаний (по умолчанию — 2 разных отрасли)"
    )
    parser.add_argument("--role", choices=agent_port.ROLES, default="cfo")
    parser.add_argument("--period", choices=list(agent_port.PERIOD_DAYS), default="week")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    setup_logging("WARNING")
    if not get_settings().database_url:
        print("Не задан адрес БД: DATABASE_URL", file=sys.stderr)
        return 2
    with get_session() as session:
        if args.companies:
            rows = [company_repo.get_by_id(session, c) for c in args.companies]
            if unknown := [c for c, r in zip(args.companies, rows, strict=True) if r is None]:
                print(f"Нет в таблице companies: {', '.join(unknown)}", file=sys.stderr)
                return 2
        else:
            rows = company_repo.first_of_distinct_industries(session, 2)
        companies = [(r.synthetic_u, r.mainokved_code, r.mainokved_name) for r in rows]
    if len(companies) < 2:
        print("В таблице companies меньше 2 компаний — сначала load_csv.py", file=sys.stderr)
        return 2

    problems = [f"сигнатура: {p}" for p in check_signature()]
    print(f"Сигнатура generate_insights{inspect.signature(agent_port.generate_insights)}")
    print("  " + ("ок" if not problems else "; ".join(problems)))
    original = os.environ.get("NEWS_SOURCE")
    try:
        for company_id, okved, name in companies:
            print(f"\n{company_id} — ОКВЭД {okved} {name}")
            for source in ("mock", "db"):
                found, line = check_run(company_id, args.role, args.period, source)
                print(f"  NEWS_SOURCE={source}: {'ок' if not found else 'НАРУШЕНИЯ'} — {line}")
                for p in found:
                    print(f"      • {p}")
                problems += [f"{company_id}/{source}: {p}" for p in found]
    finally:
        if original is None:
            os.environ.pop("NEWS_SOURCE", None)
        else:
            os.environ["NEWS_SOURCE"] = original
        get_settings.cache_clear()
    if get_settings().ml_mode == "real":
        print(f"\nML_MODE=real: адаптеры ml_ports/real.py на {companies[0][0]}")
        found = check_real(companies[0][0], args.role, args.period)
        for p in found:
            print(f"      • {p}")
        problems += [f"real: {p}" for p in found]
        get_settings.cache_clear()
    else:
        print("\nML_MODE=fake: адаптеры real-режима не проверялись (запустите с ML_MODE=real)")
    print(f"\nИтог: {'контракт соблюдён' if not problems else f'нарушений: {len(problems)}'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
