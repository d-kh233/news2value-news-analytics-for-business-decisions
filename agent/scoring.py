"""Скоринг «новость × компания × роль».

Главный признак — macro_impact промпт-инженера (factor_moves × карта ОКВЭД × продукты компании,
со знаком риск/возможность). Вокруг него: совпадение отрасли и региона, тип события, свежесть.
Слагаемые идут в трейс и в details.agent карточки. Веса черновые — подбираем в пт на тестовом наборе.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from llm.okved.sensitivity import macro_impact

from .roles import Role
from .schemas import CompanyProfile, NewsItem

# link — сила связи с компанией: максимум из макровлияния и совпадения отрасли/региона.
# Так новость про свою отрасль без макрофакторов (льготы аграриям) не проигрывает общей макроновости.
WEIGHTS = {"link": 0.55, "both": 0.10, "event_type": 0.20, "freshness": 0.15}
INDUSTRY_LINK = 0.85
REGION_LINK = 0.6
EVENT_TYPE_WEIGHT = {   # значения llm.schemas.EventType
    "monetary_policy": 1.0, "counterparty": 1.0, "regulation": 0.9, "sanctions": 0.9, "corporate": 0.9,
    "inflation": 0.8, "fx": 0.8, "commodities": 0.8, "trade": 0.8, "industry": 0.8,
    "macro": 0.7, "debt_market": 0.7, "other": 0.1,
}
# Адресные события (отраслевые, регуляторные) для чужой отрасли или региона — влияние косвенное.
# К сырью, курсу и ставке не применяем: подешевевший металл касается и стройки, хотя в новости металлурги.
TARGETED_EVENTS = {"industry", "corporate", "counterparty", "regulation"}
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


def score_news(profile: CompanyProfile, news: NewsItem, period_days: int, today: date, role: Role) -> ScoreBreakdown:
    ev = news.event
    if ev is None or ev.is_noise:
        return ScoreBreakdown(0.0, {"link": 0.0}, {"score": 0, "sign": "neutral", "why": []})
    etype = ev.event_type.value

    mi = macro_impact(ev.factor_moves, profile.row)
    macro = mi["score"] / 100
    if mi["why"] and role.factor_weight:
        total = sum(w["magnitude"] for w in mi["why"]) or 1.0
        k = sum(w["magnitude"] * role.factor_weight.get(w["factor"], 1.0) for w in mi["why"]) / total
        macro = min(1.0, macro * k)

    industry = bool(profile.okved2 and profile.okved2 in ev.okved_hints)
    region = bool(profile.region and any(r.lower() in profile.region.lower() for r in ev.regions))
    if etype in TARGETED_EVENTS and ev.okved_hints and not industry:
        macro *= OTHER_TARGET_PENALTY
    if ev.regions and not region:
        macro *= OTHER_TARGET_PENALTY

    ev_w = min(1.0, EVENT_TYPE_WEIGHT.get(etype, 0.5) * role.event_weight.get(etype, 1.0))
    fresh = math.exp(-max(0, (today - news.published_at).days) / max(period_days, 1))
    link = max(macro, INDUSTRY_LINK if industry else 0.0, REGION_LINK if region else 0.0)
    both = float((industry or region) and macro >= 0.3)
    parts = {"link": link, "both": both, "event_type": ev_w, "freshness": fresh,
             "macro": macro, "industry": float(industry), "region": float(region)}
    score = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
    if link == 0:
        score *= NO_LINK_PENALTY
    if news.relevance_score is not None:
        score = (1 - SOURCE_SCORE_SHARE) * score + SOURCE_SCORE_SHARE * news.relevance_score
    return ScoreBreakdown(round(score, 4), {k: round(v, 3) for k, v in parts.items()}, mi, industry, region)


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
    return out
