"""
Карта «ОКВЭД → чувствительность к макрофакторам» и расчёт влияния события на компанию.

Колонки okved_sensitivity.csv (черновик, проверяет аналитик):
  key_rate, inflation, labor_market, sanctions_trade  — 0..3, рост фактора вредит
  consumer_demand                                     — 0..3, рост фактора помогает
  usd_rub, oil_gas, metals, agri, gold                — −2..+2, эффект РОСТА фактора
                                                        (usd_rub up = рубль слабеет; + у экспортёров)

Использование в ранжировании (тимлид):
    from llm.okved.sensitivity import macro_impact
    res = macro_impact(event.factor_moves, company_row)
    res["score"]   # 0..100 — сколько событие «задевает» компанию через макрофакторы
    res["sign"]    # risk / opportunity / mixed / neutral
    res["why"]     # список {factor, effect, source_ids, text} — для трассировки и для промпта интерпретации
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

CSV_PATH = Path(__file__).with_name("okved_sensitivity.csv")

HARM_ON_UP = {"key_rate", "inflation", "labor_market", "sanctions_trade"}
HELP_ON_UP = {"consumer_demand"}
SIGNED = {"usd_rub", "oil_gas", "metals", "agri", "gold"}
FACTORS = HARM_ON_UP | HELP_ON_UP | SIGNED

DIR = {"up": 1.0, "down": -1.0, "unchanged": 0.0, "uncertain": 0.0}
STRENGTH = {"low": 0.5, "medium": 1.0, "high": 1.5}
# «ставку сохранили» тоже важно для закредитованных: учитываем как часть сигнала, без знака
NO_MOVE_RELEVANCE = {"unchanged": 0.5, "uncertain": 0.3}

RU = {
    "key_rate": "ключевая ставка", "usd_rub": "курс рубля", "oil_gas": "нефть и газ", "metals": "металлы",
    "agri": "агросырьё", "gold": "золото", "inflation": "инфляция", "consumer_demand": "потребительский спрос",
    "labor_market": "рынок труда", "sanctions_trade": "санкции и ВЭД",
}


@lru_cache(maxsize=1)
def load_map() -> dict[str, dict]:
    out = {}
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rec = {"name": row["name"], "note": row.get("note") or ""}
            for k in FACTORS:
                rec[k] = float(row[k])
            out[row["okved2"]] = rec
    return out


def okved2(code: str | None) -> str | None:
    if not code:
        return None
    digits = str(code).strip().split(".")[0]
    return digits.zfill(2) if digits.isdigit() else None


def _flag(company: Mapping, field: str) -> bool:
    v = company.get(field)
    return str(v).strip().lower() in {"1", "1.0", "true", "да", "yes"}


def company_multipliers(company: Mapping) -> dict[str, tuple[float, list[str]]]:
    """Усиление чувствительности по признакам компании из df_Uid_data_hack."""
    m: dict[str, tuple[float, list[str]]] = {}
    debt = [f for f in ("CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "FACTORING_MARK") if _flag(company, f)]
    if debt:
        m["key_rate"] = (1.5, [f"F:{f}" for f in debt])
    if _flag(company, "CL_PRODUCT_DEP_MARK"):
        # депозиты: рост ставки частично компенсируется доходом — ослабляем
        k, src = m.get("key_rate", (1.0, []))
        m["key_rate"] = (k * 0.85, src + ["F:CL_PRODUCT_DEP_MARK"])
    if _flag(company, "FTS_FLAG"):
        src = ["F:FTS_FLAG"] + (["F:FTS_COUNTRY_NAME"] if company.get("FTS_COUNTRY_NAME") else [])
        m["usd_rub"] = (1.5, src)
        m["sanctions_trade"] = (1.5, src)
    if _flag(company, "CL_PRODUCT_ZPP_MARK"):
        m["labor_market"] = (1.2, ["F:CL_PRODUCT_ZPP_MARK"])
    return m


def macro_impact(factor_moves: Iterable, company: Mapping) -> dict:
    code = okved2(company.get("MAINOKVED_CODE"))
    row = load_map().get(code or "")
    if row is None:
        return {"score": 0, "sign": "neutral", "why": [], "okved2": code, "note": "ОКВЭД не найден в карте"}

    mult = company_multipliers(company)
    total_signed, total_abs, why = 0.0, 0.0, []
    for fm in factor_moves:
        fm = fm.model_dump() if hasattr(fm, "model_dump") else dict(fm)
        f = fm["factor"].value if hasattr(fm["factor"], "value") else fm["factor"]
        if f not in FACTORS:
            continue
        dname = getattr(fm["direction"], "value", fm["direction"])
        d = DIR.get(dname, 0.0)
        s = STRENGTH.get(getattr(fm["strength"], "value", fm["strength"]), 1.0)
        k, extra_src = mult.get(f, (1.0, []))
        sens = row[f]
        if f in HARM_ON_UP:
            effect = -sens * d
        elif f in HELP_ON_UP:
            effect = sens * d
        else:
            effect = sens * d
        effect *= s * k
        magnitude = abs(effect) if d else abs(sens) * s * k * NO_MOVE_RELEVANCE.get(dname, 0.3)
        if magnitude == 0:
            continue
        total_signed += effect
        total_abs += magnitude
        why.append({
            "factor": f,
            "effect": round(effect, 2) + 0.0,
            "direction": dname,
            "magnitude": round(magnitude, 2),
            "source_ids": [f"F:MAINOKVED_CODE"] + extra_src,
            "text": f"{RU[f]}: отрасль «{row['name']}» чувствительна ({sens:+g})"
                    + (f", усилено признаками {', '.join(extra_src)}" if extra_src else ""),
        })

    # 4.5 ≈ сильное движение фактора с максимальной чувствительностью → 100
    score = min(100, round(total_abs / 4.5 * 100))
    if not why or all(w["effect"] == 0 for w in why):
        # нет движения (например, «ставку сохранили»): событие может быть важным, но без знака
        sign = "neutral"
    elif total_signed > 0.3 * total_abs:
        sign = "opportunity"
    elif total_signed < -0.3 * total_abs:
        sign = "risk"
    else:
        sign = "mixed"
    return {"score": score, "sign": sign, "why": sorted(why, key=lambda w: -w["magnitude"]),
            "okved2": code, "okved_name": row["name"], "note": row["note"]}
