"""
Оценка на размеченном тестовом наборе: извлечение → отбор → интерпретация → метрики.

Запуск из корня репозитория:
    python3 -m llm.eval_run \
        --companies ../agent-view/agent/data/df_Uid_data_mock.csv \
        --theses ../agent-view/agent/data/bank_theses.json \
        --out ../eval_report.md

Стоимость: 16 вызовов Gemma + до --max-insights вызовов GLM (по умолчанию 22, все размеченные пары) — несколько рублей.
Отбор здесь упрощённый (макровлияние + совпадение отрасли), как у агента Юли по смыслу;
цель — проверить, что разметка новостей и карта ОКВЭД дают правильных кандидатов.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .client import LLMCallError
from .extract import extract_event
from .insight import interpret
from .okved.sensitivity import macro_impact, okved2
from .try_insight import read_companies, render, theses_for

THRESHOLD = 40
ROLES = ["cfo", "ceo", "owner", "treasurer"]
GENERIC = ["следить за ситуацией", "проконсультироваться", "конкурент", "внимательно отслеживать"]
SIGN_OK = {"risk": {"risk", "mixed"}, "opportunity": {"opportunity", "mixed"}}


def relevance(ev, company: dict) -> tuple[int, str, str]:
    if ev.is_noise:
        return 0, "neutral", "шум"
    imp = macro_impact(ev.factor_moves, company)
    score, sign, why = imp["score"], imp["sign"], "макро"
    if okved2(company.get("MAINOKVED_CODE")) in set(ev.okved_hints) and score < 85:
        score, why = 85, "отрасль"
    return score, sign, why


def check_insight(ins, expected_sign: str) -> dict:
    words = len(ins.headline.split())
    text = " ".join([ins.headline] + [s.text for s in ins.why_it_matters + ins.what_happened]
                    + [c.text for c in ins.consequences] + [a.text for a in ins.actions]).lower()
    return {
        "sign_ok": ins.impact_type.value in SIGN_OK.get(expected_sign, {"risk", "opportunity", "mixed", "neutral"}),
        "headline_ok": words <= 16,
        "why_grounded": all(any(s[:2] in ("F:", "P:", "G:") for s in st.source_ids) for st in ins.why_it_matters),
        "has_bank_view": bool(ins.bank_view),
        "generic": [g for g in GENERIC if g in text],
        "no_revenue_numbers": not re.search(r"выручк\w* \d", text),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="llm/eval/test_set.json")
    ap.add_argument("--companies", required=True)
    ap.add_argument("--theses", required=True)
    ap.add_argument("--out", default="../eval_report.md")
    ap.add_argument("--max-insights", type=int, default=22)
    a = ap.parse_args()

    data = json.loads(Path(a.set).read_text(encoding="utf-8"))
    companies = read_companies(a.companies)
    theses = json.loads(Path(a.theses).read_text(encoding="utf-8"))

    # 1. Извлечение
    events, ext_rows = {}, []
    for n in data["news"]:
        try:
            ev = extract_event(n)
            events[n["id"]] = ev
            ext_rows.append(f"| {n['id']} | {ev.event_type.value} | {'да' if ev.is_noise else ''} | "
                            f"{', '.join(f'{m.factor.value}:{m.direction.value}' for m in ev.factor_moves) or '—'} | "
                            f"{', '.join(ev.okved_hints) or '—'} | {', '.join(ev.quality_flags) or '—'} |")
            print("извлечено", n["id"])
        except (LLMCallError, OSError) as e:
            ext_rows.append(f"| {n['id']} | ОШИБКА {str(e)[:80]} | | | | |")

    # 2. Отбор
    tp = fp = fn = 0
    sign_hits = sign_total = 0
    rel_rows = []
    for n in data["news"]:
        ev = events.get(n["id"])
        if ev is None:
            continue
        exp = n["expected"]
        found = []
        for cid, c in companies.items():
            score, sign, why = relevance(ev, c)
            if score >= THRESHOLD:
                found.append(f"{cid}({score},{sign},{why})")
                if cid in exp:
                    tp += 1
                    if sign != "neutral":
                        sign_total += 1
                        sign_hits += sign in SIGN_OK[exp[cid]]
                else:
                    fp += 1
            elif cid in exp:
                fn += 1
        missed = [c for c in exp if not any(f.startswith(c) for f in found)]
        rel_rows.append(f"| {n['id']} | {', '.join(f'{k}:{v}' for k, v in exp.items()) or 'никому'} | "
                        f"{', '.join(found) or '—'} | {', '.join(missed) or ''} |")
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0

    # 3. Интерпретация на размеченных парах
    pairs = [(n, cid, s) for n in data["news"] for cid, s in n["expected"].items() if n["id"] in events]
    pairs = pairs[:a.max_insights]
    checks, blocks, repaired, failed = [], [], 0, 0
    for i, (n, cid, exp_sign) in enumerate(pairs):
        role = ROLES[i % len(ROLES)]
        ev = events[n["id"]]
        trace: list = []
        try:
            ins, impact = interpret(ev, n["text"], companies[cid], bank_chunks=theses_for(ev, theses),
                                    role=role, trace=trace)
            fixes = [t.get("error", "")[:150] for t in trace if t.get("ok") is False]
            repaired += bool(fixes)
            ch = check_insight(ins, exp_sign)
            checks.append(ch)
            flags = [k for k, v in ch.items() if v is False] + ([f"общие фразы: {ch['generic']}"] if ch["generic"] else [])
            blocks.append(render(n, companies[cid], role, ins, impact)
                          + f"\n_Ожидалось: {exp_sign}. Проверки: {'всё ок' if not flags else ', '.join(flags)}_"
                          + (f"\n_Исправлялось:_ {fixes}" if fixes else "") + "\n")
            print("инсайт", n["id"], cid, role)
        except (LLMCallError, OSError) as e:
            failed += 1
            blocks.append(f"## {n['id']} × {cid}\nОШИБКА: {e}\n")

    k = len(checks) or 1
    share = lambda key: round(100 * sum(1 for c in checks if c[key]) / k)  # noqa: E731
    metrics = [
        ("Отбор: precision (найденные пары действительно важны)", f"{precision:.0%}"),
        ("Отбор: recall (важные пары найдены)", f"{recall:.0%}"),
        ("Отбор: знак эффекта совпал (где знак определён)", f"{sign_hits}/{sign_total}"),
        ("Интерпретация: сгенерировано / ошибок", f"{len(checks)} / {failed}"),
        ("Интерпретация: исправлялось после самопроверки", f"{repaired}/{len(checks)}"),
        ("Интерпретация: тип риск/возможность совпал с разметкой", f"{share('sign_ok')}%"),
        ("Интерпретация: «почему важно» опирается на признаки компании", f"{share('why_grounded')}%"),
        ("Интерпретация: есть позиция банка", f"{share('has_bank_view')}%"),
        ("Интерпретация: заголовок ≤ 16 слов", f"{share('headline_ok')}%"),
        ("Интерпретация: без общих фраз", f"{round(100 * sum(1 for c in checks if not c['generic']) / k)}%"),
    ]
    out = ["# Оценка на тестовом наборе\n", f"Новостей: {len(data['news'])}, компаний: {len(companies)}, "
           f"порог отбора: {THRESHOLD}.\n", "## Метрики\n", "| Метрика | Значение |", "|---|---|"]
    out += [f"| {m} | {v} |" for m, v in metrics]
    out += ["\n## Извлечение\n", "| Новость | Тип | Шум | Факторы | ОКВЭД | Флаги |", "|---|---|---|---|---|---|"] + ext_rows
    out += ["\n## Отбор\n", "| Новость | Ожидалось | Найдено (скор, знак, причина) | Пропущено |", "|---|---|---|---|"] + rel_rows
    out += ["\n## Инсайты\n"] + blocks
    Path(a.out).write_text("\n".join(out), encoding="utf-8")
    print("\n".join(f"{m}: {v}" for m, v in metrics))
    print("Отчёт:", a.out)


if __name__ == "__main__":
    main()
