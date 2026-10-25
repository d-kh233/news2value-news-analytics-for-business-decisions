"""
Дайджест компании за период по готовым инсайтам (схема llm.schemas.Digest).

# ПРАВКА feat/llm-fixes-daria: новый модуль. Схема Digest была, функции не было; бэкенду дайджест
# нужен для GET /companies/{id}/digest. Сделано по образцу llm/insight.py: тот же call_json,
# промпт в prompts/digest.system.md, проверка ссылок в цикле исправления. ML-команда может
# заменить промпт и логику своей версией, сохранив сигнатуру generate_digest.

    from llm.digest import generate_digest
    d = generate_digest(insights, company_row, "week", complete=gateway.complete)
    d.overview   # 2–3 предложения — бэкенд отдаёт как Digest.summary

insights — карточки в формате бэкенда (backend/app/schemas/insight.py), самые важные первыми.
Модель — быстрая (MODELS["fast"]): задача — сжать готовый текст, а не анализ.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .client import MODELS, CompleteFn, call_json
from .insight import company_facts
from .schemas import Digest

PROMPT_VERSION = "digest@v0"
SYSTEM = (Path(__file__).parent / "prompts" / "digest.system.md").read_text(encoding="utf-8")
PERIOD_RU = {"day": "сутки", "week": "неделя"}
TYPE_RU = {"risk": "риск", "opportunity": "возможность", "event": "событие"}
MAX_CHARS = 400   # на поле инсайта: дайджесту нужна суть, не весь текст


def digest_item_id(insight: Mapping[str, Any]) -> str:
    """id пункта дайджеста: I:<id инсайта> (по нему бэкенд узнаёт инсайт в items)."""
    return f"I:{insight['id']}"


def _cut(text: Any) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= MAX_CHARS else t[:MAX_CHARS].rsplit(" ", 1)[0] + "…"


def build_messages(insights: Sequence[Mapping[str, Any]], company: Mapping, period: str) -> tuple[list[dict], set[str]]:
    facts = company_facts(company)
    lines_company = [f"[{f['id']}] {f['text']}" for f in facts] or ["— нет данных"]
    lines = []
    for ins in insights:
        lines.append(
            f"[{digest_item_id(ins)}] {TYPE_RU.get(ins.get('type'), ins.get('type'))}, важность {ins.get('importance')}: "
            f"{_cut(ins.get('title'))}\n  Суть: {_cut(ins.get('summary'))}\n"
            f"  Для компании: {_cut(ins.get('impact_on_company'))}")
    user = "\n\n".join([
        "## КОМПАНИЯ\n" + "\n".join(lines_company),
        f"## ПЕРИОД\n{PERIOD_RU.get(period, period)}",
        "## ИНСАЙТЫ (самые важные первыми)\n" + "\n".join(lines),
        "Верни JSON дайджеста.",
    ])
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    return msgs, {digest_item_id(i) for i in insights}


def check_items(allowed: set[str]):
    def check(d: Digest) -> None:
        ids = [it.news_id for it in d.items]
        bad = sorted(set(ids) - allowed)
        if bad:
            raise ValueError(f"В items id, которых нет среди инсайтов: {bad}. Допустимы только: {sorted(allowed)}")
        missing = sorted(allowed - set(ids))
        if missing:
            raise ValueError(f"В items нет пунктов для инсайтов {missing}: нужен один пункт на каждый инсайт")
        if len(ids) != len(set(ids)):
            raise ValueError("В items повторяются id: один пункт на инсайт")
        if not d.overview.strip():
            raise ValueError("overview пустой")
    return check


def generate_digest(insights: Sequence[Mapping[str, Any]], company_profile: Mapping, period: str, *,
                    complete: Optional[CompleteFn] = None, model: Optional[str] = None,
                    trace: Optional[list] = None) -> Digest:
    """Сводка за период. insights не пустой (пустой дайджест бэкенд собирает сам, без модели)."""
    if not insights:
        raise ValueError("generate_digest: нет инсайтов — модель не вызываем")
    msgs, allowed = build_messages(insights, company_profile, period)
    digest = call_json(Digest, msgs, model=model or MODELS["fast"], complete=complete, trace=trace,
                       post_check=check_items(allowed), temperature=0.2, max_tokens=1500)
    if trace is not None:
        trace.append({"step": "digest", "prompt_version": PROMPT_VERSION, "period": period, "n": len(insights)})
    return digest
