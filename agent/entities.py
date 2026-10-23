"""Сущности компании и их поиск в новости.

Откуда берутся сущности (всё с id признака, чтобы вывод был трассируемым):
  F:FTS_COUNTRY_NAME   — страны ВЭД из данных банка («КИТАЙ, ТУРЦИЯ»);
  F:RAW_MATERIALS      — сырьё и комплектующие (профиль, ручной ввод);
  F:KEY_CLIENTS / F:KEY_SUPPLIERS — названия контрагентов в «ёлочках»;
  F:CURRENCY_RISKS     — валюты из описания валютных рисков (юань, евро, доллар);
  F:REGION / F:CITY    — регион и город регистрации;
  продукты банка       — слабые подсказки (ВЭД → «экспорт»/«импорт», зарплатный проект → «взносы»).

Слово из профиля совпадает со словом новости, если основа слова новости начинается с основы слова
профиля (agent.retrieval.stem): «Китай» находит «китайские», «экспорт» — «экспортёров».
Фраза из нескольких слов — если нашлись все её значимые слова.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .retrieval import stem, tokenize

STRONG, WEAK = 1.0, 0.5
_CURRENCIES = {"юан": "юань", "евро": "евро", "доллар": "доллар", "дирхам": "дирхам", "рупи": "рупия"}
_PRODUCT_TERMS = {
    "FTS_FLAG": ["экспорт", "импорт", "внешнеэкономическая"],
    "CL_PRODUCT_ZPP_MARK": ["страховые взносы", "фонд оплаты труда"],
    "CL_PRODUCT_LEA_MARK": ["лизинг"],
    "FACTORING_MARK": ["факторинг"],
    "CL_PRODUCT_GAR_MARK": ["гарантии"],
}
_TRUE = {"1", "1.0", "true", "да", "yes"}
# как сырьё называют в новостях: слово из профиля -> другие названия (вес чуть ниже прямого совпадения)
_SYNONYMS = {
    "сталь": ["металлопрокат", "прокат"],
    "чугун": ["металлопрокат"],
    "топливо": ["дизельное топливо", "бензин", "нефтепродукты"],
    "дизельное": ["солярка"],
    "подшипники": ["подшипники"],
    "запчасти": ["комплектующие"],
    "зерно": ["пшеница", "зерновые"],
    "упаковка": ["тара", "картон"],
}
SYNONYM = 0.8


@dataclass(frozen=True)
class Term:
    text: str          # как показать человеку
    source_id: str     # F:<КОЛОНКА>
    weight: float
    stems: tuple[str, ...]


def _term(text: str, source_id: str, weight: float, shown: str | None = None) -> Term | None:
    stems = tuple(stem(w) for w in re.findall(r"[a-zа-яё0-9]+", text, re.IGNORECASE) if len(w) >= 3)
    return Term((shown or text).strip(), source_id, weight, stems) if stems else None


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def company_terms(row: dict) -> list[Term]:
    out: list[Term | None] = []
    for c in _as_list(row.get("FTS_COUNTRY_NAME")):
        out.append(_term(c.title(), "F:FTS_COUNTRY_NAME", STRONG))
    for m in _as_list(row.get("RAW_MATERIALS")):
        out.append(_term(m, "F:RAW_MATERIALS", STRONG))
        for w in re.findall(r"[а-яё]+", m.lower()):
            out.extend(_term(alt, "F:RAW_MATERIALS", SYNONYM, shown=f"{alt} ≈ {m}") for alt in _SYNONYMS.get(w, []))
    for col in ("KEY_CLIENTS", "KEY_SUPPLIERS"):
        for name in _as_list(row.get(col)):
            quoted = re.findall(r"«([^»]+)»", name)
            out.append(_term(quoted[0] if quoted else name, f"F:{col}", STRONG))
    risks = " ".join(_as_list(row.get("CURRENCY_RISKS"))).lower()
    for key, cur in _CURRENCIES.items():
        if key in risks:
            out.append(_term(cur, "F:CURRENCY_RISKS", STRONG))
    for col, words in _PRODUCT_TERMS.items():
        if str(row.get(col, "")).strip().lower() in _TRUE:
            out.extend(_term(w, f"F:{col}", WEAK) for w in words)
    seen, uniq = set(), []
    for t in out:                                   # синоним может совпасть с исходным словом
        if t and (t.stems, t.source_id) not in seen:
            seen.add((t.stems, t.source_id))
            uniq.append(t)
    return uniq


def match(terms: list[Term], text: str) -> list[Term]:
    """Какие сущности компании упомянуты в тексте новости."""
    toks = set(tokenize(text))
    return [t for t in terms if all(any(tok.startswith(s) for tok in toks) for s in t.stems)]
