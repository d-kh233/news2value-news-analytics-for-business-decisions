"""Интерпретация одной пары «новость × компания × роль».

Промпт, контекст и проверка источников — промпт-инженера (llm.insight.interpret). Сверху агент добавляет:
  • строгую проверку (strict_check, передаётся в interpret как extra_check) — она стоит в цикле
    исправления call_json: модель видит ошибку и переписывает ответ — числа в фактах только из
    новости, «банк» только со ссылкой на A:, «факт» — на N:;
  • правку после ответа (finalize): текст позиции банка заменяется дословным тезисом, id новости
    и компании выставляет код;
  • откат на правила, если модель недоступна или ответ так и не прошёл проверку.

Итог переводится в формат бэкенда тем же адаптером, что у промпт-инженера (to_backend_insight).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from llm.backend_adapter import to_backend_insight
from llm.client import MODELS, LLMCallError
from llm.insight import interpret
from llm.okved.sensitivity import RU as FACTOR_RU
from llm.schemas import Action, Consequence, Insight, Origin, Statement

from . import rules
from .gateway import Gateway, LLMUnavailable
from .roles import Role
from .schemas import BankThesis, CompanyProfile, NewsItem
from .scoring import ScoreBreakdown

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class InsightContext:
    profile: CompanyProfile
    news: NewsItem
    breakdown: ScoreBreakdown
    why: list[dict]                 # scoring.explain() — для карточки по правилам и трейса
    theses: list[BankThesis]
    role: Role


# ---------- модель ----------

def strict_check(ctx: InsightContext):
    news_nums = set(_NUM.findall(f"{ctx.news.title} {ctx.news.text}"))

    def check(ins: Insight) -> None:
        errors = []
        for i, s in enumerate(ins.what_happened):
            extra = set(_NUM.findall(s.text)) - news_nums
            if s.origin == Origin.fact and extra:
                errors.append(f"what_happened[{i}]: числа, которых нет в тексте новости: {sorted(extra)}")
        blocks = [("what_happened", ins.what_happened), ("why_it_matters", ins.why_it_matters),
                  ("consequences", ins.consequences), ("bank_view", ins.bank_view)]
        for name, items in blocks:
            for i, s in enumerate(items):
                if s.origin == Origin.bank and not any(x.startswith("A:") for x in s.source_ids):
                    errors.append(f"{name}[{i}]: origin=bank без ссылки на аналитику банка (A:)")
                if s.origin == Origin.fact and not any(x[:2] in ("N:", "F:", "P:", "G:") for x in s.source_ids):
                    errors.append(f"{name}[{i}]: origin=fact без ссылки на новость или данные компании")
        if errors:
            raise ValueError("; ".join(errors))
    return check


def finalize(ins: Insight, ctx: InsightContext) -> list[str]:
    notes = []
    ins.news_id, ins.company_id = ctx.news.sid, ctx.profile.company_id
    by_id = {t.sid: t for t in ctx.theses}
    kept = []
    for i, s in enumerate(ins.bank_view):
        ids = [x for x in s.source_ids if x in by_id]
        if not ids:
            notes.append(f"bank_view[{i}]: нет ссылки на переданный тезис — утверждение убрано")
            continue
        if s.text.strip() != by_id[ids[0]].thesis:
            notes.append(f"bank_view[{i}]: текст заменён дословным тезисом {ids[0]}")
        kept.append(Statement(text=by_id[ids[0]].thesis, origin=Origin.bank, source_ids=ids[:1]))
    ins.bank_view = kept
    return notes


def generate(ctx: InsightContext, gw: Gateway, use_smart: bool, llm_trace: list) -> tuple[Insight, dict]:
    """→ (карточка llm.schemas.Insight, мета для трейса: path = llm | rules, notes, fallback_reason)."""
    if gw.s.llm_mode == "stub":
        return build_from_rules(ctx), {"path": "rules", "notes": ["LLM_MODE=stub: карточка собрана по правилам"]}
    if ctx.news.event is None:
        return build_from_rules(ctx), {"path": "rules", "fallback_reason": "новость не размечена",
                                       "notes": ["новость не размечена — карточка собрана по правилам"]}

    model = MODELS["smart"] if use_smart else MODELS["fast"]
    n0 = len(llm_trace)
    meta: dict = {"path": "llm", "model": model}
    try:
        ins, _impact = interpret(ctx.news.event, ctx.news.text, ctx.profile.row,
                                 bank_chunks=[t.as_chunk() for t in ctx.theses], role=ctx.role.key,
                                 complete=gw.complete, model=model, trace=llm_trace,
                                 extra_check=strict_check(ctx))
        notes = finalize(ins, ctx)
        fails = [x.get("error") for x in llm_trace[n0:] if x.get("ok") is False]
        if fails:
            notes.insert(0, "исправлено после самопроверки: " + " | ".join(f[:200] for f in fails))
        return ins, {**meta, "notes": notes}
    except LLMUnavailable as e:
        reason = str(e)
    except LLMCallError as e:
        reason = f"ответ не прошёл проверку: {str(e)[:300]}"
    return build_from_rules(ctx), {**meta, "path": "rules", "fallback_reason": reason,
                                   "notes": [f"модель недоступна или ответ отклонён ({reason}); карточка по правилам"]}


def to_backend(ins: Insight, ctx: InsightContext, resolve_source, generated_by: str, extra: dict) -> dict:
    """Карточка в формате бэкенда. id стабилен для пары компания × новость × роль."""
    insight_id = f"{ctx.profile.company_id}-{ctx.news.news_id}-{ctx.role.key}"
    out = to_backend_insight(ins, insight_id, resolve_source, relevance_score=round(ctx.breakdown.score * 100),
                             role=ctx.role.key, generated_by=generated_by)
    out["details"]["agent"] = extra
    return out


# ---------- без модели ----------

_SIGN_WORD = {"risk": "Риск", "opportunity": "Возможность", "mixed": "Риски и возможности", "neutral": "Важно учесть"}


def _used_features(ctx: InsightContext) -> list[str]:
    seen: list[str] = []
    for w in ctx.why:
        for s in w["source_ids"]:
            if s not in seen:
                seen.append(s)
    return seen


def _first_sentences(text: str, n: int = 2) -> str:
    return " ".join(re.split(r"(?<=[.!?])\s+", text.strip())[:n])


def build_from_rules(ctx: InsightContext) -> Insight:
    b, n = ctx.breakdown, ctx.news
    factored = [w for w in ctx.why if w.get("factor")]
    factored.sort(key=lambda w: -w["magnitude"] * ctx.role.factor_weight.get(w["factor"], 1.0))
    sign = b.macro.get("sign", "neutral")
    top = factored[0]["factor"] if factored else None
    where = f"для отрасли «{ctx.profile.industry}»" if ctx.profile.industry else "для вашей компании"
    headline = f"{_SIGN_WORD[sign]}: {FACTOR_RU[top]} — {where}" if top else f"{_SIGN_WORD['neutral']}: событие {where}"

    consequences, actions = [], []
    for w in factored[:2]:
        tpl = rules.RULES.get(w["factor"])
        if not tpl:
            continue
        kind = "risk" if w["effect"] < 0 else "opportunity" if w["effect"] > 0 else "factor"
        mag = w["magnitude"]
        consequences.append(Consequence(
            text=tpl[kind], kind=kind, level="high" if mag >= 3 else "medium" if mag >= 1.5 else "low",
            horizon="short", origin=Origin.ai, source_ids=[n.sid, *w["source_ids"]]))
        text, product = tpl["action"]
        actions.append(Action(text=text, type="action", bank_product=product, source_ids=[n.sid]))
    if not consequences:
        consequences.append(Consequence(
            text="Прямого влияния через макрофакторы карта отраслей не показывает; событие стоит учесть как контекст рынка.",
            kind="factor", level="low", horizon="medium", origin=Origin.ai, source_ids=[n.sid]))

    why = [Statement(text=w["text"], origin=Origin.ai, source_ids=w["source_ids"]) for w in ctx.why] or [
        Statement(text="Событие отобрано по типу и свежести; прямой связи с признаками компании не найдено.",
                  origin=Origin.ai, source_ids=[n.sid])]
    gaps = ["карточка собрана по правилам без модели: формулировки общие для отрасли"]
    if not ctx.profile.okved2:
        gaps.append("нет ОКВЭД компании в карте отраслей — влияние через макрофакторы не оценено")
    if not ctx.theses:
        gaps.append("в аналитике банка нет тезиса по этой теме")

    return Insight(
        news_id=n.sid, company_id=ctx.profile.company_id, headline=headline,
        impact_type=sign, importance="high" if b.score >= 0.7 else "medium" if b.score >= 0.5 else "low",
        confidence="low",
        what_happened=[Statement(text=_first_sentences(n.text), origin=Origin.fact, source_ids=[n.sid])],
        why_it_matters=why, consequences=consequences, actions=actions,
        bank_view=[Statement(text=t.thesis, origin=Origin.bank, source_ids=[t.sid]) for t in ctx.theses],
        company_features_used=_used_features(ctx), data_gaps=gaps,
    )
