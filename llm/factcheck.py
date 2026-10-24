"""
Проверка фактов в готовых инсайтах (демо-сценарий) — без вызова моделей, бесплатно.

Берёт результат агента (python -m agent run ... --json) или список инсайтов в формате бэкенда
и сверяет с первоисточниками:
  1. каждое число в «Что произошло», «Позиция банка» и заголовке есть в новости или тезисе, на которые ссылается утверждение;
  2. утверждения «Позиция банка» ссылаются на существующий тезис банка;
  3. все ссылки (N:, A:) ведут на существующие источники;
  4. тезис банка старше новости больше чем на 60 дней — должна быть оговорка (в последствиях или пробелах).

Запуск из корня репозитория с веткой агента:
    python -m agent run --company MOCK-002 --role cfo --json > run_002.json
    python -m llm.factcheck run_002.json [run_004.json ...] \
        --news agent/data/mock_news.json --theses agent/data/bank_theses.json --out factcheck.md

Каждая отмеченная строка — повод открыть карточку и прочитать глазами. Это не гарантия ошибки:
например, «II квартал» или «14,0% → 14%» иногда дают ложную тревогу.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path

NUM = re.compile(r"\d+(?:[.,]\d+)?")
STALE_DAYS = 60
STALE_WORDS = ("устарел", "расход", "противореч", "не отражает", "до события", "ранее", "свеж")


def _norm_num(x: str) -> str:
    x = x.replace(",", ".")
    if "." in x:
        x = x.rstrip("0").rstrip(".")
    return x


def numbers(text: str) -> set[str]:
    text = re.sub(r"(?<=\d)[\s  ](?=\d{3}\b)", "", text or "")   # 11 300 → 11300
    return {_norm_num(n) for n in NUM.findall(text)}


def _date(v) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            return None


def load_sources(news_paths: list[str], theses_path: str) -> dict[str, dict]:
    src: dict[str, dict] = {}
    for p in news_paths:
        for n in json.loads(Path(p).read_text(encoding="utf-8")):
            src[f"N:{n['id']}"] = {"text": f"{n.get('title', '')}. {n.get('text', '')}", "date": _date(n.get("published_at"))}
    for t in json.loads(Path(theses_path).read_text(encoding="utf-8")):
        src[f"A:{t['thesis_id']}"] = {"text": f"{t.get('title', '')}. {t.get('thesis', '')} {t.get('figure', '')} {t.get('date', '')}",
                                      "date": _date(t.get("published_at"))}
    return src


def _base_id(sid: str) -> str:
    # A:BA-12:1 → A:BA-12 (номер фрагмента), N:M01 → N:M01
    return ":".join(sid.split(":")[:2])


def load_insights(paths: list[str]) -> list[dict]:
    out = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        items = data.get("insights", data) if isinstance(data, dict) else data
        out.extend(items)
    return out


def check(card: dict, src: dict[str, dict]) -> list[str]:
    d = card.get("details") or {}
    issues = []
    stmts = [("Что произошло", s) for s in d.get("what_happened", [])] + \
            [("Позиция банка", s) for s in d.get("bank_view", [])] + \
            [("Почему важно", s) for s in d.get("why_it_matters", [])] + \
            [("Последствия", s) for s in d.get("consequences", [])]

    for block, s in stmts:
        ids = [_base_id(x) for x in s.get("source_ids", []) if x[:2] in ("N:", "A:")]
        for x in ids:
            if x not in src:
                issues.append(f"{block}: ссылка на неизвестный источник {x}")
        if block == "Позиция банка" and not any(x.startswith("A:") for x in ids):
            issues.append(f"{block}: нет ссылки на тезис банка — «{s.get('text', '')[:80]}»")
        if block in ("Что произошло", "Позиция банка") or s.get("origin") in ("fact", "bank"):
            have = set().union(*(numbers(src[x]["text"]) for x in ids if x in src)) if ids else set()
            missing = sorted(numbers(s.get("text", "")) - have)
            if missing and ids:
                issues.append(f"{block}: числа {missing} нет в источниках {ids} — «{s.get('text', '')[:90]}»")

    # заголовок: числа должны быть хоть в каком-то источнике карточки
    all_ids = {_base_id(x) for s in (b for _, b in stmts) for x in s.get("source_ids", []) if x[:2] in ("N:", "A:")}
    have_all = set().union(*(numbers(src[x]["text"]) for x in all_ids if x in src)) if all_ids else set()
    miss_h = sorted(numbers(card.get("title", "")) - have_all)
    if miss_h:
        issues.append(f"Заголовок: числа {miss_h} нет в источниках — «{card.get('title', '')}»")

    # устаревшая аналитика без оговорки
    news_dates = [src[x]["date"] for x in all_ids if x.startswith("N:") and x in src and src[x]["date"]]
    bank_dates = [src[x]["date"] for x in all_ids if x.startswith("A:") and x in src and src[x]["date"]]
    if news_dates and bank_dates and (max(news_dates) - min(bank_dates)).days > STALE_DAYS:
        text = " ".join([c.get("text", "") for c in d.get("consequences", [])] + d.get("data_gaps", [])).lower()
        if not any(w in text for w in STALE_WORDS):
            issues.append(f"Тезис банка старше новости на {(max(news_dates) - min(bank_dates)).days} дн., оговорки нет")
    return issues


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="JSON от python -m agent run --json или список инсайтов")
    ap.add_argument("--news", nargs="+", default=["agent/data/mock_news.json"])
    ap.add_argument("--theses", default="agent/data/bank_theses.json")
    ap.add_argument("--out", default="factcheck.md")
    a = ap.parse_args()

    src = load_sources(a.news, a.theses)
    cards = load_insights(a.runs)
    lines = ["# Проверка фактов в инсайтах\n", f"Карточек: {len(cards)}. Источников: {len(src)}.\n"]
    bad = 0
    for c in cards:
        issues = check(c, src)
        bad += bool(issues)
        mark = "⚠️" if issues else "✓"
        lines.append(f"## {mark} {c.get('id', '?')} — {c.get('title', '')}")
        lines += [f"- {i}" for i in issues] or ["- замечаний нет"]
        lines.append("")
    lines.insert(2, f"С замечаниями: {bad} из {len(cards)}.\n")
    Path(a.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"Карточек: {len(cards)}, с замечаниями: {bad}. Отчёт: {a.out}")


if __name__ == "__main__":
    main()
