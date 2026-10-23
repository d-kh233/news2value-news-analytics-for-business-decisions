"""
Перевод нашего подробного инсайта (llm.schemas.Insight) в формат бэкенда
(backend/app/schemas/insight.py, ветка feat/backend).

Верхний уровень — ровно поля Даши, фронт работает с ними как с моками.
Полная структура из 6 блоков и разметка «факт / банк / ИИ» уходит в
необязательные поля (details, used_profile_fields, prompt_version, generated_by, role),
которые нужно добавить в схему бэкенда.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from .schemas import ImpactType, Insight, Level, Origin

PROMPT_VERSION = "insight@v2"   # меняем при каждой правке промптов: по нему бэкенд сбрасывает кэш

IMPORTANCE = {Level.high: 85, Level.medium: 60, Level.low: 35}
TYPE = {ImpactType.risk: "risk", ImpactType.opportunity: "opportunity",
        ImpactType.mixed: "event", ImpactType.neutral: "event"}
ORIGIN_RU = {Origin.fact: "Факт", Origin.bank: "Позиция Альфа-Банка", Origin.ai: "Вывод ИИ"}

# resolve_source(source_id) -> {"title", "url", "published_at", "source_kind"} | None
SourceResolver = Callable[[str], Optional[dict]]


def _join(statements) -> str:
    return " ".join(s.text.strip() for s in statements if s.text.strip())


def to_backend_insight(ins: Insight, insight_id: str, resolve_source: SourceResolver,
                       relevance_score: Optional[int] = None, role: Optional[str] = None,
                       generated_by: str = "llm") -> dict:
    """
    relevance_score — скор ранжирования 0..100 (если есть, важнее уровня из LLM).
    resolve_source — функция бэкенда: по id вида N:123 / A:doc:chunk отдаёт данные документа.
    Признаки компании (F:, P:, G:) в sources не попадают — они идут в used_profile_fields.
    """
    importance = IMPORTANCE[ins.importance]
    if relevance_score is not None:
        importance = round(0.6 * relevance_score + 0.4 * importance)

    sources, seen = [], set()
    for sid in sorted(ins.all_source_ids()):
        if sid[:2] not in ("N:", "A:") or sid in seen:
            continue
        seen.add(sid)
        doc = resolve_source(sid)
        if doc:
            sources.append({k: doc[k] for k in ("title", "url", "published_at", "source_kind")})

    action = next((a.text for a in ins.actions if a.type == "action"), None) \
        or (ins.actions[0].text if ins.actions else "Отслеживать развитие ситуации")

    bank = _join(ins.bank_view)
    impact = _join(ins.why_it_matters)

    return {
        "id": insight_id,
        "company_id": ins.company_id,
        "title": ins.headline,
        "summary": _join(ins.what_happened),
        "type": TYPE[ins.impact_type],
        "importance": max(0, min(100, importance)),
        "impact_on_company": impact if not bank else f"{impact} Позиция банка: {bank}",
        "recommended_action": action,
        "sources": sources,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # --- необязательные поля (добавить в схему бэкенда) ---
        "details": ins.model_dump(mode="json"),
        "used_profile_fields": sorted(s for s in ins.all_source_ids() if s[:2] in ("F:", "P:", "G:")),
        "prompt_version": PROMPT_VERSION,
        "generated_by": generated_by,
        "role": role,
    }
