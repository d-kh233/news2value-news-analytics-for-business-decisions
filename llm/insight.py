"""
Шаг 4 агента: персонализированная интерпретация события (GLM).

Главная функция для бэкенда — generate_insight(news, company_profile, ...),
сигнатура как в документе бэкенда (B5). Возвращает dict в формате backend Insight
(через backend_adapter). Подробный объект (6 блоков) лежит в поле details.

Низкоуровнево: interpret(event, company, ...) -> llm.schemas.Insight.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from .backend_adapter import PROMPT_VERSION, SourceResolver, to_backend_insight
from .client import MODELS, CompleteFn, call_json
from .okved.sensitivity import macro_impact, okved2
from .schemas import EventExtraction, Insight

SYSTEM_TEMPLATE = (Path(__file__).parent / "prompts" / "insight.system.md").read_text(encoding="utf-8")

ROLES = {
    "owner": "Собственник: стоимость бизнеса, стратегия, риски для капитала и группы компаний, долгосрочные сценарии.",
    "ceo": "Генеральный директор: выручка, спрос, конкуренты, цепочки поставок, инвестпроекты, операционные решения.",
    "cfo": "Финансовый директор: стоимость и структура долга, процентные расходы, валютные риски, ликвидность, ковенанты.",
    "treasurer": "Казначейство: денежные потоки, размещение свободных средств, хеджирование, платёжный календарь, лимиты.",
    "accountant": "Главный бухгалтер: налоги, регуляторные требования, отчётность, документооборот по ВЭД.",
    "procurement": "Закупки и логистика: поставщики, цены сырья и комплектующих, курс при импорте, сроки и маршруты поставок.",
}
DEFAULT_ROLE = "ceo"

# Признаки компании, которые показываем модели. Ключи — как в CSV (регистр любой).
FEATURES = [
    ("MAINOKVED_CODE", "Код основного ОКВЭД"),
    ("MAINOKVED_NAME", "Основной вид деятельности"),
    ("REVENUE_COMPANY", "Выручка есть в данных (число не называть, единицы неизвестны)"),
    ("REGION", "Регион регистрации"),
    ("CITY", "Город регистрации"),
    ("ORG_TYPE", "Тип организации"),
    ("OKFS_NAME", "Форма собственности"),
    ("COMPANY_AGE_YEARS_GRADATION", "Возраст компании, лет"),
    ("ECO_MARK_LABEL", "Стадия развития бизнеса"),
    ("AMOUNT_WORKERS_FNS_GRADATION", "Число работников (ФНС)"),
    ("CL_TYPE", "Тип клиента банка"),
    ("CL_PRODUCT_RKO_MARK", "Есть расчётно-кассовое обслуживание"),
    ("CL_PRODUCT_LOA_MARK", "Есть кредитные продукты"),
    ("CL_PRODUCT_LEA_MARK", "Есть лизинг"),
    ("CL_PRODUCT_GAR_MARK", "Есть банковские гарантии"),
    ("CL_PRODUCT_DEP_MARK", "Есть депозиты"),
    ("CL_PRODUCT_ZPP_MARK", "Есть зарплатный проект"),
    ("FACTORING_MARK", "Есть факторинг"),
    ("FTS_FLAG", "Ведёт внешнеэкономическую деятельность (есть в базе ФТС)"),
    ("FTS_COUNTRY_NAME", "Страны ВЭД по данным ФТС (экспорт или импорт — в данных не указано)"),
    ("GROUP_ID", "Входит в группу компаний"),
]
FLAGS = {k for k, _ in FEATURES if k.endswith("_MARK") or k == "FTS_FLAG"}
EMPTY = {"", "nan", "none", "null", "не определено"}


def _get(company: Mapping, key: str) -> Any:
    for k in (key, key.lower()):
        if k in company and company[k] is not None:
            return company[k]
    md = company.get("manual_data") or {}
    return md.get(key) or md.get(key.lower())


def _truthy(v: Any) -> Optional[bool]:
    s = str(v).strip().lower()
    if s in {"1", "1.0", "true", "да", "yes"}:
        return True
    if s in {"0", "0.0", "false", "нет", "no"}:
        return False
    return None


def company_facts(company: Mapping) -> list[dict]:
    """Признаки компании с id F:<ПОЛЕ>. Пустые и «нет» по флагам не показываем (кроме важных отсутствий)."""
    facts = []
    for key, label in FEATURES:
        v = _get(company, key)
        if v is None or str(v).strip().lower() in EMPTY:
            continue
        if key in FLAGS:
            t = _truthy(v)
            if t is None:
                continue
            if not t:
                if key in ("CL_PRODUCT_LOA_MARK", "FTS_FLAG"):
                    facts.append({"id": f"F:{key}", "text": f"{label}: нет"})
                continue
            facts.append({"id": f"F:{key}", "text": label})
            continue
        if key == "REVENUE_COMPANY":
            facts.append({"id": f"F:{key}", "text": label})
            continue
        if key == "GROUP_ID":
            facts.append({"id": f"G:{str(v).strip()}", "text": f"{label} {str(v).strip()}"})
            continue
        facts.append({"id": f"F:{key}", "text": f"{label}: {str(v).strip()}"})
    return facts


def _block(title: str, items: Iterable[str]) -> str:
    items = list(items)
    return f"## {title}\n" + ("\n".join(items) if items else "— нет данных")


def build_context(event: EventExtraction, news_text: str, company: Mapping,
                  counterparties: Iterable[Mapping] = (), bank_chunks: Iterable[Mapping] = (),
                  related_news: Iterable[Mapping] = (), role: str = DEFAULT_ROLE,
                  max_news_chars: int = 3000) -> tuple[str, set[str], dict]:
    """
    Собирает пользовательское сообщение и множество допустимых id источников.
    counterparties: [{"id", "direction": "покупатель|поставщик", "okved_name", "share_pct"}]
    bank_chunks:    [{"id": "A:doc:chunk", "title", "date", "text"}]
    related_news:   [{"id": "N:..", "title", "date", "summary"}]
    """
    allowed: set[str] = {f"N:{event.news_id}"}

    ev = event.model_dump(mode="json", exclude={"model", "prompt_version", "url", "news_id"}, exclude_none=True)
    lines_event = [
        f"id: N:{event.news_id}",
        "Разметка: " + json.dumps(ev, ensure_ascii=False),
        "Текст новости: " + (news_text or "")[:max_news_chars],
    ]

    facts = company_facts(company)
    allowed |= {f["id"] for f in facts}
    allowed.add("F:MAINOKVED_CODE")
    lines_company = [f"[{f['id']}] {f['text']}" for f in facts]

    lines_cp = []
    for cp in counterparties:
        pid = cp["id"] if str(cp["id"]).startswith("P:") else f"P:{cp['id']}"
        allowed.add(pid)
        share = f", доля в оборотах {cp['share_pct']}%" if cp.get("share_pct") is not None else ""
        lines_cp.append(f"[{pid}] {cp.get('direction', 'контрагент')}: {cp.get('okved_name') or 'отрасль неизвестна'}{share}")

    impact = macro_impact(event.factor_moves, {k.upper(): v for k, v in company.items() if isinstance(k, str)})
    lines_impact = [f"Отрасль по ОКВЭД {impact.get('okved2') or '—'}: {impact.get('okved_name', 'нет в карте')}. "
                    f"Сила влияния {impact['score']}/100, знак: {impact['sign']}."]
    for w in impact["why"]:
        eff = (f"эффект {w['effect']:+g} (плюс — выгодно, минус — вредно)" if w["effect"]
               else "направление неясно" if w.get("direction") == "uncertain"
               else "фактор не сдвинулся, но остаётся значимым")
        lines_impact.append(f"- {w['text']}; {eff} ({', '.join(w['source_ids'])})")
    if impact.get("note"):
        lines_impact.append(f"Примечание к отрасли: {impact['note']}")

    lines_bank = []
    for ch in bank_chunks:
        allowed.add(ch["id"])
        lines_bank.append(f"[{ch['id']}] «{ch.get('title', '')}» ({ch.get('date', 'дата неизвестна')}): {ch['text']}")

    lines_rel = []
    for rn in related_news:
        allowed.add(rn["id"])
        lines_rel.append(f"[{rn['id']}] {rn.get('date', '')} {rn.get('title', '')}: {rn.get('summary', '')}")

    role_txt = ROLES.get(role, ROLES[DEFAULT_ROLE])
    msg = "\n\n".join([
        _block("СОБЫТИЕ", lines_event),
        _block("КОМПАНИЯ", lines_company + lines_cp),
        _block("КАНАЛЫ ВЛИЯНИЯ (предварительный расчёт)", lines_impact),
        _block("АНАЛИТИКА БАНКА", lines_bank),
        _block("СВЯЗАННЫЕ НОВОСТИ", lines_rel),
        f"## РОЛЬ ЧИТАТЕЛЯ\n{role_txt}",
        f"## ДОПУСТИМЫЕ ID ИСТОЧНИКОВ\n{', '.join(sorted(allowed))}",
        f'Верни JSON инсайта. news_id = "N:{event.news_id}", company_id = "{_get(company, "SYNTHETIC_U") or company.get("company_id") or company.get("id")}".',
    ])
    return msg, allowed, impact


def system_prompt(role: str = DEFAULT_ROLE) -> str:
    guide = "\n".join(f"- {k}: {v}" for k, v in ROLES.items())
    return SYSTEM_TEMPLATE.replace("{role_guide}", guide)


def check_sources(allowed: set[str]):
    def check(ins: Insight) -> None:
        bad = sorted(s for s in ins.all_source_ids() if s not in allowed)
        if bad:
            raise ValueError(f"Ссылки на несуществующие источники: {bad}. Допустимы только: {sorted(allowed)}")
        if not ins.company_features_used and not any(
                s[:2] in ("F:", "P:", "G:") for st in ins.why_it_matters for s in st.source_ids):
            raise ValueError("В why_it_matters нет опоры на признаки компании (F:/P:/G:). Объясни связь с этой компанией.")
    return check


# старые имена — чтобы код агента (agent/generator.py) работал без правок
_system = system_prompt
_check_sources = check_sources


def interpret(event: EventExtraction, news_text: str, company: Mapping, *, counterparties=(), bank_chunks=(),
              related_news=(), role: str = DEFAULT_ROLE, complete: Optional[CompleteFn] = None,
              model: Optional[str] = None, trace: Optional[list] = None,
              extra_check: Optional[Callable[[Insight], None]] = None) -> tuple[Insight, dict]:
    """extra_check(ins) — доп. проверка агента; бросает ValueError, модель увидит текст и исправит ответ."""
    user, allowed, impact = build_context(event, news_text, company, counterparties, bank_chunks, related_news, role)
    msgs = [{"role": "system", "content": system_prompt(role)}, {"role": "user", "content": user}]
    base = check_sources(allowed)

    def post_check(ins: Insight) -> None:
        base(ins)
        if extra_check is not None:
            extra_check(ins)

    ins = call_json(Insight, msgs, model=model or MODELS["smart"], complete=complete, trace=trace,
                    post_check=post_check, temperature=0.2, max_tokens=4000)
    if trace is not None:
        trace.append({"step": "interpret", "prompt_version": PROMPT_VERSION, "role": role,
                      "macro_impact": {"score": impact["score"], "sign": impact["sign"]},
                      "allowed_sources": sorted(allowed)})
    return ins, impact


def generate_insight(news: Mapping, company_profile: Mapping, *, event: Optional[EventExtraction] = None,
                     resolve_source: Optional[SourceResolver] = None, counterparties=(), bank_chunks=(),
                     related_news=(), role: str = DEFAULT_ROLE, relevance_score: Optional[int] = None,
                     insight_id: Optional[str] = None, complete: Optional[CompleteFn] = None,
                     trace: Optional[list] = None) -> dict:
    """
    Для бэкенда (B5). news: {id, title, text, source, url, published_at}.
    Если event не передан, сначала вызывается извлечение (Gemma).
    resolve_source(id) -> {title, url, published_at, source_kind} — бэкенд знает свои документы;
    по умолчанию источник собирается из самой новости и bank_chunks.
    """
    from .extract import extract_event

    if event is None:
        event = extract_event(dict(news), complete=complete, trace=trace)

    if resolve_source is None:
        known = {f"N:{news['id']}": {"title": news.get("title", ""), "url": news.get("url") or "",
                                     "published_at": news.get("published_at"), "source_kind": "external"}}
        for ch in bank_chunks:
            known[ch["id"]] = {"title": ch.get("title", ""), "url": ch.get("url") or "",
                               "published_at": ch.get("published_at") or ch.get("date"), "source_kind": "bank_analytics"}
        resolve_source = lambda sid: known.get(sid) if known.get(sid, {}).get("published_at") else None  # noqa: E731

    ins, impact = interpret(event, news.get("text", ""), company_profile, counterparties=counterparties,
                            bank_chunks=bank_chunks, related_news=related_news, role=role,
                            complete=complete, trace=trace)
    score = relevance_score if relevance_score is not None else impact["score"]
    return to_backend_insight(ins, insight_id or f"{ins.company_id}:{event.news_id}", resolve_source,
                              relevance_score=score, role=role)
