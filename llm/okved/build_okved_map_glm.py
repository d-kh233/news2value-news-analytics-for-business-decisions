"""
Черновик карты ОКВЭД через GLM и сверка с ручным черновиком.

Запуск (нужен .env с LLM_BASE_URL и LLM_API_KEY):
    python -m llm.okved.build_okved_map_glm            # все коды, пачками по 12
    python -m llm.okved.build_okved_map_glm 24 46 47   # только эти коды

Результат:
    okved/okved_sensitivity_glm.csv  — оценки GLM с обоснованиями
    okved/okved_diff.md              — где GLM и ручной черновик расходятся на 2+ балла.
                                       Это список для аналитика: смотреть надо только его.
Стоимость: ~8 запросов к GLM, укладывается в малую часть дневного лимита.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field, conint

from ..client import MODELS, call_json
from .sensitivity import CSV_PATH, load_map

OUT = Path(__file__).with_name("okved_sensitivity_glm.csv")
DIFF = Path(__file__).with_name("okved_diff.md")
COLS = ["key_rate", "usd_rub", "oil_gas", "metals", "agri", "gold", "inflation",
        "consumer_demand", "labor_market", "sanctions_trade"]


class Row(BaseModel):
    okved2: str
    key_rate: conint(ge=-3, le=3)
    usd_rub: conint(ge=-3, le=3)
    oil_gas: conint(ge=-3, le=3)
    metals: conint(ge=-3, le=3)
    agri: conint(ge=-3, le=3)
    gold: conint(ge=-3, le=3)
    inflation: conint(ge=-3, le=3)
    consumer_demand: conint(ge=-3, le=3)
    labor_market: conint(ge=-3, le=3)
    sanctions_trade: conint(ge=0, le=3)
    rationale: str = Field(description="1–2 предложения: главные каналы влияния")


class Table(BaseModel):
    rows: List[Row]


SYSTEM = """Ты — отраслевой аналитик корпоративного банка в России. Оцени, насколько типичная КРУПНАЯ российская компания (выручка от 15 млрд руб.) из раздела ОКВЭД 2 чувствительна к макрофакторам.

Шкалы: для всех факторов, кроме sanctions_trade, — эффект РОСТА фактора для отрасли от −3 до +3 (минус — вредит, плюс — помогает).
- key_rate: рост ключевой ставки (у закредитованных и капиталоёмких −, у банков может быть +).
- usd_rub: рост курса доллара, то есть ослабление рубля (+ у экспортёров, − у импортёров).
- oil_gas, metals, agri, gold: рост цен (+ если отрасль продаёт, − если покупает как издержки).
- inflation: рост инфляции. labor_market: перегрев рынка труда, рост зарплат. consumer_demand: рост потребительского спроса.
- sanctions_trade (0..3): уязвимость к ужесточению санкций, пошлин, ограничений ВЭД.

Ставь 0, если связь слабая. Не завышай. Верни JSON {"rows": [...]} по одной строке на каждый код из запроса, в том же порядке."""


def run(codes: List[str], batch: int = 12) -> None:
    base = load_map()
    rows: list[Row] = []
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        listing = "\n".join(f"{c} — {base[c]['name']}" for c in chunk if c in base)
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Коды ОКВЭД:\n" + listing}]
        t = call_json(Table, msgs, model=MODELS["smart"], temperature=0.1, max_tokens=3000)
        rows.extend(t.rows)
        print(f"готово {min(i + batch, len(codes))}/{len(codes)}")

    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["okved2", "name", *COLS, "rationale"])
        for r in rows:
            w.writerow([r.okved2, base.get(r.okved2, {}).get("name", ""), *[getattr(r, c) for c in COLS], r.rationale])

    lines = ["# Расхождения GLM и ручного черновика (|разница| ≥ 2)", "",
             "| ОКВЭД | Отрасль | Фактор | Черновик | GLM | Обоснование GLM |", "|---|---|---|---|---|---|"]
    n = 0
    for r in rows:
        b = base.get(r.okved2)
        if not b:
            continue
        for c in COLS:
            if abs(b[c] - getattr(r, c)) >= 2:
                n += 1
                lines.append(f"| {r.okved2} | {b['name']} | {c} | {b[c]:g} | {getattr(r, c)} | {r.rationale} |")
    lines.insert(2, f"Найдено расхождений: {n}. Аналитику: решить, какое значение оставить, и поправить {CSV_PATH.name}.\n")
    DIFF.write_text("\n".join(lines), encoding="utf-8")
    print(f"Записано: {OUT.name}, {DIFF.name} (расхождений: {n})")


if __name__ == "__main__":
    args = sys.argv[1:]
    run([a.zfill(2) for a in args] if args else sorted(load_map().keys()))
