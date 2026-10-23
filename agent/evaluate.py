"""Проверка качества отбора: python -m agent eval [--set agent/eval/selection_set.json] [--json]

Для каждого случая «компания × роль» прогоняет агента (режим stub — модель не вызывается, отбор и
скоринг те же, что в бою) и сравнивает ленту с разметкой.

Метрики на ленте из top_k карточек:
  P@k     — доля уместных (оценка ≥ 1) среди показанных;
  R@k     — доля обязательных (оценка 2), которые попали в ленту;
  nDCG@k  — учитывает порядок: обязательное внизу ленты хуже, чем наверху;
  шум     — сколько показанных карточек разметка считает лишними;
  дубли   — сколько пар перепечаток одного события попало в ленту вместе.
Это метрики критерия «персонализация» и черновик для текста метрик (критерии 07, 08).
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, Settings
from .orchestrator import Orchestrator
from .profiles import row_from_backend_profile
from .schemas import CompanyProfile

DEFAULT_SET = Path(__file__).parent / "eval" / "selection_set.json"


def _backend_profiles() -> dict[str, CompanyProfile]:
    mocks = REPO_ROOT / "data" / "mocks"
    companies = {c["id"]: c for c in json.loads((mocks / "companies.json").read_text(encoding="utf-8"))}
    profiles = {p["company_id"]: p for p in json.loads((mocks / "profiles.json").read_text(encoding="utf-8"))}
    return {cid: CompanyProfile.from_row(row_from_backend_profile(c, profiles.get(cid, {})), cid)
            for cid, c in companies.items()}


def _ndcg(ranked: list[str], rel: dict[str, int], k: int) -> float:
    dcg = sum((2 ** rel.get(n, 0) - 1) / math.log2(i + 2) for i, n in enumerate(ranked[:k]))
    ideal = sorted(rel.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def evaluate(set_path: str | Path = DEFAULT_SET, settings: Settings | None = None) -> dict[str, Any]:
    spec = json.loads(Path(set_path).read_text(encoding="utf-8"))
    base = settings or Settings.from_env()
    s = replace(base, llm_mode="stub", today=spec["as_of"], top_k_news=spec.get("top_k", base.top_k_news))
    orch = Orchestrator(s)
    backend = _backend_profiles()
    dup_pairs = [tuple(p) for p in spec.get("duplicates", [])]
    k = s.top_k_news

    rows = []
    for case in spec["cases"]:
        cid, role, rel = case["company"], case["role"], case["relevant"]
        company = backend[cid] if case.get("profile_source") == "backend_mocks" else cid
        r = orch.run(company, spec.get("period_days", 30), k, role)
        shown = [c["details"]["news_id"].removeprefix("N:") for c in r.insights]
        must = {n for n, g in rel.items() if g >= 2}
        rows.append({
            "company": cid, "role": role, "shown": shown,
            "P@k": sum(1 for n in shown if rel.get(n, 0) >= 1) / len(shown) if shown else 0.0,
            "R@k": len(must & set(shown)) / len(must) if must else 1.0,
            "nDCG@k": _ndcg(shown, rel, k),
            "noise": [n for n in shown if rel.get(n, 0) == 0],
            "missed": sorted(must - set(shown)),
            "dups": sum(1 for a, b in dup_pairs if a in shown and b in shown),
        })
    n = len(rows) or 1
    summary = {m: round(sum(x[m] for x in rows) / n, 3) for m in ("P@k", "R@k", "nDCG@k")}
    summary.update(noise_cards=sum(len(x["noise"]) for x in rows), dup_pairs=sum(x["dups"] for x in rows),
                   cases=len(rows), k=k)
    return {"summary": summary, "cases": rows}


def print_report(res: dict[str, Any]) -> None:
    s = res["summary"]
    print(f"\nОтбор на {s['cases']} случаях, лента top-{s['k']}:  P@k {s['P@k']:.2f} · R@k {s['R@k']:.2f} · "
          f"nDCG@k {s['nDCG@k']:.2f} · лишних карточек {s['noise_cards']} · дублей {s['dup_pairs']}\n")
    for x in res["cases"]:
        flag = "" if not x["missed"] and not x["noise"] else "  ←"
        print(f"{x['company']:<9} {x['role']:<11} P {x['P@k']:.2f}  R {x['R@k']:.2f}  nDCG {x['nDCG@k']:.2f}  "
              f"лента {x['shown']}"
              + (f"  пропущено {x['missed']}" if x["missed"] else "")
              + (f"  лишнее {x['noise']}" if x["noise"] else "") + flag)
