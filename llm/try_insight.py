"""
Пробный прогон интерпретации на настоящей модели: новость × компания × роль → отчёт для чтения глазами.

Запуск из корня репозитория:
    python3 -m llm.try_insight \
        --companies ../agent-view/agent/data/df_Uid_data_mock.csv \
        --theses ../agent-view/agent/data/bank_theses.json \
        --out ../insight_run.md

По умолчанию 5 пар (≈5 вызовов GLM + извлечение Gemma, копейки). Свои пары: --pairs t2:MOCK-006:cfo t6:MOCK-005:ceo
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .client import LLMCallError
from .extract import extract_event
from .insight import interpret

DEFAULT_PAIRS = ["t2:MOCK-006:cfo", "t2:MOCK-004:ceo", "t6:MOCK-005:ceo", "t6:MOCK-002:cfo", "t1:MOCK-001:owner"]


def read_companies(path: str) -> dict[str, dict]:
    text = Path(path).read_text(encoding="utf-8-sig")
    rows = csv.DictReader(text.splitlines(), delimiter=";" if text.count(";") > text.count(",") else ",")
    out = {}
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        out[r["SYNTHETIC_U"]] = r
    return out


def theses_for(event, theses: list[dict], limit: int = 3) -> list[dict]:
    """Простой отбор тезисов банка по совпадению факторов (в агенте Юли — свой поиск)."""
    factors = {fm.factor.value for fm in event.factor_moves}
    hit = [t for t in theses if factors & set(t.get("factors", []))]
    return [{"id": f"A:{t['thesis_id']}:1", "title": t["title"], "date": t.get("date"),
             "published_at": t.get("published_at"), "url": t.get("url"),
             "text": f"{t['thesis']} ({t.get('figure', '')})"} for t in hit[:limit]]


def render(news, company, role, ins, impact) -> str:
    L = [f"## {news['id']} × {company['SYNTHETIC_U']} ({company.get('MAINOKVED_NAME', '')}) × {role}",
         f"**Новость:** {news['title']}",
         f"**Макровлияние:** {impact['score']}/100, {impact['sign']}",
         f"### {ins.headline}",
         f"`{ins.impact_type.value}` · важность `{ins.importance.value}` · уверенность `{ins.confidence.value}`"]

    def block(title, items, fmt):
        L.append(f"**{title}**")
        L.extend(f"- {fmt(x)}" for x in items) if items else L.append("- —")

    st = lambda s: f"[{s.origin.value}] {s.text} {s.source_ids}"  # noqa: E731
    block("01 Что произошло", ins.what_happened, st)
    block("02 Почему важно", ins.why_it_matters, st)
    block("03 Последствия", ins.consequences,
          lambda c: f"[{c.origin.value}/{c.kind}/{c.level.value}/{c.horizon.value}] {c.text} {c.source_ids}")
    block("04 Действия", ins.actions, lambda a: f"[{a.type}] {a.text}" + (f" → {a.bank_product}" if a.bank_product else ""))
    block("05 Позиция банка", ins.bank_view, st)
    L.append(f"**06 Признаки:** {ins.company_features_used}")
    block("Пробелы в данных", ins.data_gaps, str)
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--news", default="llm/tests/test_news.json")
    ap.add_argument("--companies", required=True)
    ap.add_argument("--theses", required=True)
    ap.add_argument("--out", default="../insight_run.md")
    ap.add_argument("--pairs", nargs="*", default=DEFAULT_PAIRS)
    a = ap.parse_args()

    news = {n["id"]: n for n in json.loads(Path(a.news).read_text(encoding="utf-8"))}
    companies = read_companies(a.companies)
    theses = json.loads(Path(a.theses).read_text(encoding="utf-8"))

    parts, events = ["# Пробный прогон интерпретации\n"], {}
    for p in a.pairs:
        nid, cid, role = p.split(":")
        try:
            if nid not in events:
                events[nid] = extract_event(news[nid])
            ev = events[nid]
            trace: list = []
            ins, impact = interpret(ev, news[nid]["text"], companies[cid], bank_chunks=theses_for(ev, theses),
                                    role=role, trace=trace)
            fixes = [t.get("error", "")[:200] for t in trace if t.get("ok") is False]
            parts.append(render(news[nid], companies[cid], role, ins, impact)
                         + (f"\n_Исправлялось после проверки:_ {fixes}\n" if fixes else ""))
            print("ok", p)
        except (LLMCallError, OSError, KeyError) as e:
            parts.append(f"## {p}\nОШИБКА: {e}\n")
            print("ОШИБКА", p, str(e)[:200])
    Path(a.out).write_text("\n".join(parts), encoding="utf-8")
    print("Отчёт:", a.out)


if __name__ == "__main__":
    main()
