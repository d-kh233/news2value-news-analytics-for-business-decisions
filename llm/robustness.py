"""
Проверка на шуме и неполных данных (ограничение 05 кейса) на настоящих моделях.

Запуск: python3 -m llm.robustness            (~8 вызовов Gemma/GLM, копейки)

Сценарии и ожидаемое поведение:
  1. реклама                 → is_noise, инсайт не строится
  2. обрезанная новость      → флаг truncated, факты без домыслов, инсайт с осторожностью
  3. компания без ОКВЭД      → инсайт строится, в data_gaps про отрасль, confidence не high
  4. пустой профиль          → нет выдуманных признаков: «почему важно» не может опереться на F: →
                               либо честный отказ (LLMCallError) либо только общие признаки
  5. противоречие с банком    → расхождение названо в consequences/data_gaps
"""
from __future__ import annotations

from .client import LLMCallError
from .extract import extract_event
from .insight import interpret

AD = {"id": "r1", "title": "Скидки 70% на всё!", "text": "Только сегодня! Распродажа офисной техники, скидки до 70%. Звоните!",
      "published_at": "2026-10-20", "source": "демо"}
CUT = {"id": "r2", "title": "Правительство обсуждает квоты на экспорт удобрений", "published_at": "2026-10-20", "source": "демо",
       "text": "Правительство рассматривает введение квот на экспорт азотных удобрений с 1 декабря. По данным источников, объём квоты может составить"}
RATE = {"id": "r3", "title": "ЦБ снизил ключевую ставку до 13%", "published_at": "2026-10-24", "source": "демо",
        "text": "Банк России снизил ключевую ставку на 100 б.п., до 13% годовых, указав на замедление инфляции."}
BANK_RATE = [{"id": "A:BA-12:1", "title": "Ключевая ставка ЦБ РФ: ставка без изменения на 14,0%, риторика стала жестче",
              "date": "11 сентября 2026", "published_at": "2026-09-11T00:00:00Z",
              "text": "Ключевая ставка сохранена, риторика ЦБ ужесточилась. Ожидается удержание текущего уровня до конца года. (14,0%)"}]
CHEM = {"SYNTHETIC_U": "R-1", "MAINOKVED_CODE": "20.15", "MAINOKVED_NAME": "Производство удобрений", "FTS_FLAG": 1,
        "FTS_COUNTRY_NAME": "БРАЗИЛИЯ", "CL_PRODUCT_LOA_MARK": 1}
NO_OKVED = {"SYNTHETIC_U": "R-2", "CL_PRODUCT_LOA_MARK": 1, "REGION": "Москва"}
EMPTY = {"SYNTHETIC_U": "R-3"}
BUILDER = {"SYNTHETIC_U": "R-4", "MAINOKVED_CODE": "41.20", "MAINOKVED_NAME": "Строительство жилых и нежилых зданий",
           "CL_PRODUCT_LOA_MARK": 1}


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK ' if ok else 'НЕТ'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> None:
    res = []
    ev = extract_event(AD)
    res.append(check("1. реклама распознана как шум", ev.is_noise, f"event_type={ev.event_type.value}"))

    ev = extract_event(CUT)
    res.append(check("2a. обрезанный текст помечен", "truncated" in ev.quality_flags, str(ev.quality_flags)))
    ins, _ = interpret(ev, CUT["text"], CHEM, role="ceo")
    res.append(check("2b. инсайт по обрезанной новости не уверен на high", ins.confidence.value != "high",
                     f"confidence={ins.confidence.value}, gaps={ins.data_gaps[:2]}"))

    ev = extract_event(RATE)
    ins, _ = interpret(ev, RATE["text"], NO_OKVED, role="cfo")
    res.append(check("3. без ОКВЭД: инсайт есть, отрасль в пробелах", any("отрасл" in g.lower() or "оквэд" in g.lower()
                                                                       or "вид деятельности" in g.lower() for g in ins.data_gaps),
                     f"gaps={ins.data_gaps[:3]}"))

    try:
        ins, _ = interpret(ev, RATE["text"], EMPTY, role="cfo")
        feats = ins.company_features_used
        res.append(check("4. пустой профиль: нет выдуманных признаков, пробел назван",
                         not feats and any("профил" in g.lower() for g in ins.data_gaps),
                         f"features={feats}, gaps={ins.data_gaps[:2]}"))
    except LLMCallError as e:
        res.append(check("4. пустой профиль: честный отказ вместо выдумки", True, str(e)[:120]))

    ins, _ = interpret(ev, RATE["text"], BUILDER, bank_chunks=BANK_RATE, role="cfo")
    text = " ".join([c.text for c in ins.consequences] + ins.data_gaps + [s.text for s in ins.why_it_matters]).lower()
    res.append(check("5. расхождение новости и аналитики банка названо",
                     any(w in text for w in ("расход", "противореч", "устарел", "до снижения", "ожидал")),
                     "; ".join(ins.data_gaps[:2])))
    print(f"\nИтого: {sum(res)}/{len(res)}")


if __name__ == "__main__":
    main()
