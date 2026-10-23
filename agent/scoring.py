"""Скоринг «новость × компания × роль».

Признаки (все раскладываются в трейс и в details.agent карточки):
  macro     — macro_impact промпт-инженера: factor_moves × карта ОКВЭД аналитика × продукты компании;
  industry  — новость про отрасль компании (okved_hints);
  region    — событие в регионе компании;
  entity    — в новости упомянуты сущности компании: страны ВЭД, сырьё, контрагенты, валюты (entities.py);
  sim       — лексическая близость новости к профилю (BM25, считается по всем кандидатам периода);
  event_type, freshness.
link = max(macro, отрасль, регион, сущности) — сила связи с компанией; без связи новость режется.
Веса подобраны на размеченном наборе agent/eval/selection_set.json (python -m agent eval).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from llm.okved.sensitivity import macro_impact

from .entities import WEAK, Term, match
from .retrieval import tokenize
from .roles import Role
from .schemas import CompanyProfile, NewsItem

# link — сила связи с компанией: максимум из макровлияния и совпадения отрасли/региона.
# Так новость про свою отрасль без макрофакторов (льготы аграриям) не проигрывает общей макроновости.
WEIGHTS = {"link": 0.50, "both": 0.10, "sim": 0.10, "event_type": 0.15, "freshness": 0.15}
INDUSTRY_LINK = 0.85
REGION_LINK = 0.6
MACRO_SCALE = 4.0          # величина влияния, при которой macro ≈ 0.63
ENTITY_LINK = 0.8          # × вес сущности: страна/сырьё/контрагент 1.0, подсказка по продукту 0.5
EVENT_TYPE_WEIGHT = {   # значения llm.schemas.EventType
    "monetary_policy": 1.0, "counterparty": 1.0, "regulation": 0.9, "sanctions": 0.9, "corporate": 0.9,
    "inflation": 0.8, "fx": 0.8, "commodities": 0.8, "trade": 0.8, "industry": 0.8,
    "macro": 0.7, "debt_market": 0.7, "other": 0.1,
}
# Широкие события — ставка, инфляция, курс, сырьё, долговой рынок — касаются всех отраслей, даже если
# в новости названа одна: подешевевший металл касается и стройки, хотя в новости металлурги.
# Все остальные (отраслевые, регуляторные, торговые меры, «макро» про одну отрасль вроде зарплат
# разработчиков) адресные: если в новости названа чужая отрасль или чужой регион, влияние косвенное.
# Сырьё, контрагенты и валюты компании штраф не снимает: подорожание китайских подшипников важно
# покупателю подшипников из любой отрасли.
BROAD_EVENTS = {"monetary_policy", "inflation", "fx", "commodities", "debt_market"}
# Страна ВЭД — канал влияния только там, где она про торговлю и расчёты. «Сталь в Китае подешевела» —
# это цена, а не торговля с Китаем, совпадение страны тут ничего не значит.
COUNTRY_EVENTS = {"trade", "sanctions", "regulation", "fx", "counterparty", "corporate"}
_DIRECT_ENTITIES = {"F:RAW_MATERIALS", "F:KEY_CLIENTS", "F:KEY_SUPPLIERS", "F:CURRENCY_RISKS"}
RULE_EVENTS = {"regulation", "trade", "sanctions"}
OTHER_TARGET_PENALTY = 0.3
NO_LINK_PENALTY = 0.2
SOURCE_SCORE_SHARE = 0.5   # если источник новостей прислал свой relevance_score, смешиваем пополам


@dataclass
class ScoreBreakdown:
    score: float
    parts: dict[str, float]
    macro: dict = field(default_factory=dict)      # результат macro_impact: score, sign, why
    industry_match: bool = False
    region_match: bool = False
    entities: list[Term] = field(default_factory=list)


def score_news(profile: CompanyProfile, news: NewsItem, period_days: int, today: date, role: Role,
               terms: list[Term] | None = None, sim: float = 0.0) -> ScoreBreakdown:
    ev = news.event
    if ev is None or ev.is_noise:
        return ScoreBreakdown(0.0, {"link": 0.0}, {"score": 0, "sign": "neutral", "why": []})
    etype = ev.event_type.value

    mi = macro_impact(ev.factor_moves, profile.row)
    # Счёт macro_impact упирается в 100: на решении по ставке у застройщика с кредитом и у IT без
    # кредита одинаково 100, ранжировать нечем. Берём сумму величин влияния (с весами роли) и
    # насыщаем плавно: 2 → 0.39, 4.5 → 0.68, 6 → 0.78, 11 → 0.94.
    total = sum(w["magnitude"] * role.factor_weight.get(w["factor"], 1.0) for w in mi["why"])
    macro = 1 - math.exp(-total / MACRO_SCALE)

    industry = bool(profile.okved2 and profile.okved2 in ev.okved_hints)
    region = bool(profile.region and any(_same_region(r, profile.region) for r in ev.regions))
    found = match(terms or [], " ".join([news.title, news.text, *ev.countries, *ev.actors, *ev.instruments]))
    if etype not in RULE_EVENTS:            # «экспорт», «взносы» как подсказка — только для правил и мер
        found = [t for t in found if t.weight > WEAK]
    if etype not in COUNTRY_EVENTS:
        found = [t for t in found if t.source_id != "F:FTS_COUNTRY_NAME"]
    if etype == "fx" and _has_fx_exposure(profile.row) and not _currency_mismatch(profile.row, news):
        # курс рубля прямо касается всех, кто ведёт ВЭД или имеет валютные обязательства
        found = found + [_FX_TERM]
    direct = max((role.term_weight.get(t.source_id, 1.0) * t.weight for t in found
                  if t.source_id in _DIRECT_ENTITIES), default=0.0)
    indirect = max((role.term_weight.get(t.source_id, 1.0) * t.weight for t in found
                    if t.source_id not in _DIRECT_ENTITIES), default=0.0)
    region_link = REGION_LINK if region else 0.0

    targeted = etype not in BROAD_EVENTS
    if targeted and ev.okved_hints and not industry:          # событие чужой отрасли
        macro *= OTHER_TARGET_PENALTY
        region_link *= OTHER_TARGET_PENALTY
        indirect *= OTHER_TARGET_PENALTY
    if ev.regions and not region:                             # событие в чужом регионе
        macro *= OTHER_TARGET_PENALTY
        if targeted:                                          # региональная программа — не для нас
            industry = False
            indirect *= OTHER_TARGET_PENALTY
    if etype in RULE_EVENTS and _country_mismatch(profile.row, ev.countries):
        macro *= OTHER_TARGET_PENALTY                         # мера про Турцию, а у компании ВЭД с Китаем
    entity = min(1.0, max(direct, indirect))

    # роль может поднять тип события выше 1: для бухгалтера правило про НДС важнее цен на сырьё
    ev_w = min(1.5, EVENT_TYPE_WEIGHT.get(etype, 0.5) * role.event_weight.get(etype, 1.0))
    if etype == "fx" and _currency_mismatch(profile.row, news):
        macro *= OTHER_TARGET_PENALTY
    fresh = math.exp(-max(0, (today - news.published_at).days) / max(period_days, 1))
    link = max(macro, INDUSTRY_LINK if industry else 0.0, region_link, ENTITY_LINK * entity)
    both = float((industry or region or entity >= 1.0) and macro >= 0.3)
    parts = {"link": link, "both": both, "sim": sim, "event_type": ev_w, "freshness": fresh,
             "macro": macro, "industry": float(industry), "region": float(region), "entity": entity}
    score = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
    if link == 0:
        score *= NO_LINK_PENALTY
    if news.relevance_score is not None:
        score = (1 - SOURCE_SCORE_SHARE) * score + SOURCE_SCORE_SHARE * news.relevance_score
    return ScoreBreakdown(round(score, 4), {k: round(v, 3) for k, v in parts.items()}, mi, industry, region, found)


# валюты: основа в тексте -> как называем; страны ВЭД -> валюта расчётов (если в профиле её не указали)
_CUR_STEMS = {"юан": "юань", "евр": "евро", "доллар": "доллар", "лир": "лира", "тенге": "тенге",
              "рупи": "рупия", "дирхам": "дирхам"}
_COUNTRY_CUR = {"кита": "юань", "турц": "лира", "сша": "доллар", "казахст": "тенге", "инди": "рупия",
                "оаэ": "дирхам", "герман": "евро", "итали": "евро", "франци": "евро", "нидерланд": "евро"}


def company_currencies(row: dict) -> set[str]:
    out = {cur for s, cur in _CUR_STEMS.items() if s in " ".join(tokenize(" ".join(map(str, row.get("CURRENCY_RISKS") or []))))}
    for tok in tokenize(str(row.get("FTS_COUNTRY_NAME") or "")):
        out |= {cur for s, cur in _COUNTRY_CUR.items() if tok.startswith(s)}
    return out


def _currency_mismatch(row: dict, news: NewsItem) -> bool:
    """Новость про курс конкретной валюты, а у компании известны другие валюты расчётов.
    Доллар не считаем: «рубль укрепился до 85 за доллар» — это общий курс рубля, он касается всех."""
    mine = company_currencies(row)
    toks = set(tokenize(f"{news.title} {news.text}"))
    theirs = {cur for s, cur in _CUR_STEMS.items() if s in toks} - {"доллар"}
    return bool(mine and theirs and not (mine & theirs))


_TRUE = {"1", "1.0", "true", "да", "yes"}
_FX_TERM = Term("компания ведёт ВЭД или имеет валютные обязательства", "F:FTS_FLAG", 1.0, ())


def _has_fx_exposure(row: dict) -> bool:
    return str(row.get("FTS_FLAG", "")).strip().lower() in _TRUE or bool(company_currencies(row))


_RUSSIA = set(tokenize("Россия РФ"))


def _country_mismatch(row: dict, countries: list[str]) -> bool:
    """Мера касается конкретных стран, страны ВЭД компании известны и ни одна не совпала."""
    named = {t for c in countries for t in tokenize(c)} - _RUSSIA
    mine = {t for t in tokenize(str(row.get("FTS_COUNTRY_NAME") or ""))}
    if not named or not mine:
        return False
    return not any(a.startswith(b) or b.startswith(a) for a in named for b in mine)


def _same_region(a: str, b: str) -> bool:
    """«Татарстан» ~ «Республика Татарстан», «Новосибирская область» ~ «Новосибирская обл.»"""
    x, y = set(tokenize(a)) - _REGION_WORDS, set(tokenize(b)) - _REGION_WORDS
    return bool(x and y and (x <= y or y <= x))


_REGION_WORDS = set(tokenize("республика область край обл автономный округ город г"))


def explain(profile: CompanyProfile, b: ScoreBreakdown) -> list[dict]:
    """Почему новость касается компании: [{text, source_ids, factor?, effect?, magnitude?}].
    Для трейса и для карточки по правилам (у модели свой расчёт внутри llm.insight)."""
    facts = profile.facts()
    out = []
    if b.industry_match:
        out.append({"text": f"Событие касается отрасли компании: {profile.industry}",
                    "source_ids": ["F:MAINOKVED_CODE"]})
    for w in b.macro.get("why", []):
        ids = [s for s in w["source_ids"] if s in facts or s == "F:MAINOKVED_CODE"]
        out.append({"text": w["text"], "source_ids": ids, "factor": w["factor"], "effect": w["effect"],
                    "magnitude": w["magnitude"]})
    if b.region_match:
        out.append({"text": f"Событие в регионе компании: {profile.region}", "source_ids": ["F:REGION"]})
    for t in b.entities:
        out.append({"text": f"В новости упомянуто «{t.text}» — {_ENTITY_WHY.get(t.source_id, 'признак компании')}",
                    "source_ids": [t.source_id]})
    return out


_ENTITY_WHY = {
    "F:FTS_COUNTRY_NAME": "страна ВЭД компании",
    "F:RAW_MATERIALS": "сырьё или комплектующие компании",
    "F:KEY_CLIENTS": "ключевой клиент компании",
    "F:KEY_SUPPLIERS": "ключевой поставщик компании",
    "F:CURRENCY_RISKS": "валюта, в которой у компании есть обязательства или выручка",
    "F:FTS_FLAG": "компания ведёт ВЭД",
    "F:CL_PRODUCT_ZPP_MARK": "у компании зарплатный проект",
    "F:CL_PRODUCT_LEA_MARK": "у компании есть лизинг",
    "F:FACTORING_MARK": "у компании есть факторинг",
    "F:CL_PRODUCT_GAR_MARK": "у компании есть банковские гарантии",
}


def company_query(profile: CompanyProfile) -> str:
    """Текст профиля для лексической близости: отрасль, вид деятельности, сырьё, цели."""
    r = profile.row
    parts = [profile.industry or "", str(r.get("MAINOKVED_NAME") or "")]
    for col in ("RAW_MATERIALS", "STRATEGIC_GOALS"):
        v = r.get(col)
        parts.extend(v if isinstance(v, list) else [str(v)] if v else [])
    return " ".join(p for p in parts if p)
