"""Запуск агента без бэкенда (разработка, отладка промптов, запись кэша для жюри).

  python -m agent run --company MOCK-002 --role cfo          # карточки в консоли
  python -m agent run --company MOCK-002 --role cfo --json   # то, что получит бэкенд
  python -m agent companies                                  # компании из COMPANIES_CSV
  python -m agent inspect-csv data/raw/df_Uid_data_hack.csv  # что увидит модель по реальному файлу
  python -m agent roles
  python -m agent eval                                       # качество отбора на размеченном наборе
  python -m agent record-demo                                # кэш для жюри: компании API × все роли
  LLM_MODE=replay python -m agent record-demo                # проверка, что кэш полный
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Settings
from .profiles import ProfileStore, detect_csv_kind, read_csv
from .roles import ROLES


def _print_human(r) -> None:
    print(f"\n=== {r.profile.short()}")
    print(f"роль: {r.role} · LLM: {r.llm_mode} · период {r.period_days} дн. · карточек: {len(r.insights)} · run {r.run_id}\n")
    for c in r.insights:
        d, a = c["details"], c["details"]["agent"]
        print(f"[{c['importance']:>3} {c['type']:<11}] {c['id']} ({c['generated_by']}) — {c['title']}")
        for s in d["what_happened"]:
            print(f"  01 ФАКТ: {s['text']}  {s['source_ids']}")
        for s in d["why_it_matters"]:
            print(f"  02 ПОЧЕМУ ВАМ: {s['text']}  {s['source_ids']}")
        for x in d["consequences"]:
            print(f"  03 {x['kind'].upper()} ({x['level']}): {x['text']}")
        for x in d["actions"]:
            print(f"  04 ДЕЙСТВИЕ: {x['text']}" + (f" [{x['bank_product']}]" if x.get("bank_product") else ""))
        for s in d["bank_view"]:
            print(f"  05 БАНК: {s['text']}  {s['source_ids']}")
        print(f"  06 ПРИЗНАКИ: {d['company_features_used']}")
        print(f"  источники: {[s['title'] for s in c['sources']]}")
        if a["validation_notes"]:
            print("  проверка: " + " | ".join(a["validation_notes"]))
        print()
    print("трейс: " + " → ".join(f"{t.name}[{t.status}]" for t in r.trace))
    print("LLM: " + str({k: v for k, v in r.llm_usage.items() if k != "calls_log"}))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--company", required=True)
    r.add_argument("--period", type=int, default=30)
    r.add_argument("--top-k", type=int)
    r.add_argument("--role", default="ceo", help=" | ".join(ROLES))
    r.add_argument("--json", action="store_true", help="полный JSON прогона вместо краткого вида")
    sub.add_parser("companies")
    sub.add_parser("roles")
    ev = sub.add_parser("eval")
    ev.add_argument("--set", default=None, help="по умолчанию agent/eval/selection_set.json")
    ev.add_argument("--json", action="store_true")
    ic = sub.add_parser("inspect-csv")
    ic.add_argument("path")
    rd = sub.add_parser("record-demo", help="прогон компаний из API (data/mocks) по ролям — запись или проверка кэша")
    rd.add_argument("--roles", default=",".join(ROLES), help="через запятую; по умолчанию все")
    rd.add_argument("--companies", default="", help="через запятую; по умолчанию все из data/mocks/companies.json")
    a = ap.parse_args(argv)

    if a.cmd == "roles":
        print(json.dumps({k: {"title": v.title, "focus": v.focus} for k, v in ROLES.items()}, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "inspect-csv":
        header, rows = read_csv(a.path)
        out = {"kind": detect_csv_kind(header), "n_rows": len(rows), "header": header}
        if out["kind"] == "companies":
            out.update(ProfileStore(a.path).describe())
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    s = Settings.from_env()
    if a.cmd == "eval":
        from .evaluate import DEFAULT_SET, evaluate, print_report
        res = evaluate(a.set or DEFAULT_SET, s)
        print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else "", end="")
        if not a.json:
            print_report(res)
        return 0
    if a.cmd == "record-demo":
        return _record_demo(s, a.roles, a.companies)
    if a.cmd == "companies":
        print(json.dumps(ProfileStore(s.companies_csv).list_brief(1000), ensure_ascii=False, indent=2))
        return 0

    from .orchestrator import Orchestrator
    res = Orchestrator(s).run(a.company, a.period, a.top_k, a.role)
    if a.json:
        print(res.model_dump_json(indent=2))
    else:
        _print_human(res)
    return 0


def _record_demo(s: Settings, roles: str, companies: str) -> int:
    """Те же профили и параметры, что у бэкенда (agent_port.generate_insights, период 30 дней),
    поэтому записанное здесь бэкенд в replay найдёт. live — пишет кэш, replay — проверяет, что всё есть."""
    from .evaluate import _backend_profiles
    from .orchestrator import Orchestrator

    if s.llm_mode == "stub":
        print("LLM_MODE=stub: модель не вызывается. Для записи — LLM_MODE=live, для проверки — replay.")
        return 2
    if not s.today:
        print("Задайте AGENT_TODAY (дата, с которой будет работать сервер в replay), например 2026-10-22.")
        return 2
    profiles = _backend_profiles()
    cids = [c.strip() for c in companies.split(",") if c.strip()] or list(profiles)
    role_list = [r.strip() for r in roles.split(",") if r.strip()]
    orch = Orchestrator(s)
    print(f"LLM_MODE={s.llm_mode} · AGENT_TODAY={s.today} · smart={int(s.llm_use_smart)} · "
          f"critic={int(s.llm_use_critic)} · {len(cids)} компаний × {len(role_list)} ролей")
    total = by_rules = missed = 0
    for cid in cids:
        for role in role_list:
            r = orch.run(profiles[cid], 30, role=role)
            rules = [c["id"] for c in r.insights if c["generated_by"] != "llm"]
            verdicts = [c["details"]["agent"].get("review", {}).get("verdict") for c in r.insights]
            total, by_rules = total + len(r.insights), by_rules + len(rules)
            missed += r.llm_usage.get("missed", 0)
            print(f"  {cid:<6} {role:<12} карточек {len(r.insights)}, по шаблонам {len(rules)}, "
                  f"критик {verdicts}, новых вызовов {r.llm_usage.get('calls', 0)}, из кэша {r.llm_usage.get('replayed', 0)}, "
                  f"≈{r.llm_usage.get('cost_rub_est', 0)} ₽")
    print(f"Итого карточек {total}, по шаблонам {by_rules}.")
    if s.llm_mode == "replay":
        if missed:
            print(f"В кэше нет {missed} ответов: перезапустите с LLM_MODE=live (записанное повторно не оплачивается).")
            return 1
        print("Кэш полный: сервер в replay покажет те же карточки.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
