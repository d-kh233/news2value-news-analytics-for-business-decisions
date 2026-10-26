"""
Ответ на вопрос руководителя по одному инсайту (схема llm.schemas.ChatAnswer).

# ПРАВКА feat/llm-fixes-daria: новый модуль. Схема ChatAnswer была, функции не было; бэкенду ответ
# нужен для POST /insights/{id}/ask (выполняет worker). Сделано по образцу llm/insight.py: тот же
# call_json, промпт в prompts/qa.system.md, проверка ссылок в цикле исправления. ML-команда может
# заменить промпт и логику своей версией, сохранив сигнатуру answer_question.

    from llm.qa import answer_question, source_index
    a = answer_question(insight, "Что делать с кредитом?", history, complete=gateway.complete)
    a.answer, a.statements, a.out_of_scope

insight — карточка в формате бэкенда (с details из 6 блоков, если её сделал агент; у заглушки и
моков details нет — тогда опора только на верхние поля и sources).
history — прошлые вопросы по этому инсайту: [{"question", "answer"}], старые первыми.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .client import MODELS, CompleteFn, call_json
from .insight import company_facts
from .schemas import ChatAnswer

PROMPT_VERSION = "qa@v1"   # v1: категории продуктов банка под профиль, без служебных id в тексте
SYSTEM = (Path(__file__).parent / "prompts" / "qa.system.md").read_text(encoding="utf-8")
HISTORY_TURNS = 5
ORIGIN_RU = {"fact": "факт", "bank": "позиция банка", "ai": "вывод ИИ"}
BLOCKS = [("what_happened", "01 Что произошло"), ("why_it_matters", "02 Почему важно для компании"),
          ("consequences", "03 Последствия"), ("actions", "04 Что можно сделать"),
          ("bank_view", "05 Позиция Альфа-Банка")]


def source_index(insight: Mapping[str, Any]) -> dict[str, dict]:
    """id источника -> источник. У источников без id (моки, старые карточки) id = S:<номер>."""
    out: dict[str, dict] = {}
    for i, src in enumerate(insight.get("sources") or [], start=1):
        out[src.get("id") or f"S:{i}"] = dict(src)
    return out


def _details_lines(details: Mapping[str, Any]) -> tuple[list[str], set[str]]:
    lines, ids = [], set()
    for key, title in BLOCKS:
        items = details.get(key) or []
        if not items:
            continue
        lines.append(f"{title}:")
        for it in items:
            sids = list(it.get("source_ids") or [])
            ids |= set(sids)
            origin = ORIGIN_RU.get(it.get("origin"), it.get("type") or "")
            product = f" (продукт банка: {it['bank_product']})" if it.get("bank_product") else ""
            lines.append(f"- ({origin}) {it.get('text', '')}{product} [{', '.join(sids)}]".replace(" []", ""))
    for gap in details.get("data_gaps") or []:
        lines.append(f"Пробел в данных: {gap}")
    return lines, ids | set(details.get("company_features_used") or [])


def build_messages(insight: Mapping[str, Any], question: str, history: Sequence[Mapping[str, Any]],
                   company: Optional[Mapping] = None) -> tuple[list[dict], set[str]]:
    sources = source_index(insight)
    allowed: set[str] = set(sources)
    lines_ins = [f"Заголовок: {insight.get('title', '')}", f"Суть: {insight.get('summary', '')}",
                 f"Вывод для компании: {insight.get('impact_on_company', '')}",
                 f"Рекомендуемое действие: {insight.get('recommended_action', '')}"]
    details = insight.get("details") or {}
    if details:
        more, ids = _details_lines(details)
        lines_ins += more
        allowed |= {i for i in ids if i[:2] in ("N:", "A:", "F:", "P:", "G:")}
    lines_src = [f"[{sid}] {s.get('title', '')} ({s.get('source_kind', '')}, {s.get('published_at', '')})"
                 for sid, s in sources.items()] or ["— нет источников"]
    facts = company_facts(company or {})
    allowed |= {f["id"] for f in facts}
    lines_company = [f"[{f['id']}] {f['text']}" for f in facts]
    lines_company += [f"[{fid}] признак, использованный в разборе" for fid in sorted(allowed)
                      if fid[:2] in ("F:", "P:", "G:") and fid not in {f["id"] for f in facts}]
    lines_hist = [f"Вопрос: {h.get('question', '')}\nОтвет: {h.get('answer', '')}"
                  for h in list(history)[-HISTORY_TURNS:] if h.get("answer")]
    user = "\n\n".join([
        "## ИНСАЙТ\n" + "\n".join(lines_ins),
        "## ИСТОЧНИКИ\n" + "\n".join(lines_src),
        "## ПРИЗНАКИ КОМПАНИИ\n" + ("\n".join(lines_company) or "— нет данных"),
        "## ПРЕДЫДУЩИЕ ВОПРОСЫ\n" + ("\n\n".join(lines_hist) or "— нет"),
        f"## ДОПУСТИМЫЕ ID ИСТОЧНИКОВ\n{', '.join(sorted(allowed)) or '— нет'}",
        f"## ВОПРОС\n{question.strip()}",
    ])
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], allowed


def check_answer(allowed: set[str]):
    def check(a: ChatAnswer) -> None:
        if not a.answer.strip():
            raise ValueError("answer пустой")
        bad = sorted({s for st in a.statements for s in st.source_ids} - allowed)
        if bad:
            raise ValueError(f"Ссылки на несуществующие источники: {bad}. Допустимы только: {sorted(allowed)}")
    return check


def answer_question(insight: Mapping[str, Any], question: str, history: Sequence[Mapping[str, Any]] = (), *,
                    company_profile: Optional[Mapping] = None, complete: Optional[CompleteFn] = None,
                    model: Optional[str] = None, trace: Optional[list] = None) -> ChatAnswer:
    """Ответ по инсайту. Источники ответа — statements[].source_ids; их данные даёт source_index(insight)."""
    if not question.strip():
        raise ValueError("answer_question: пустой вопрос")
    msgs, allowed = build_messages(insight, question, history, company_profile)
    ans = call_json(ChatAnswer, msgs, model=model or MODELS["fast"], complete=complete, trace=trace,
                    post_check=check_answer(allowed), temperature=0.2, max_tokens=1200)
    if trace is not None:
        trace.append({"step": "qa", "prompt_version": PROMPT_VERSION, "out_of_scope": ans.out_of_scope})
    return ans


def answer_sources(insight: Mapping[str, Any], ans: ChatAnswer) -> list[dict]:
    """Источники инсайта, на которые сослался ответ; ссылок нет (out_of_scope, только F:) — все источники."""
    index = source_index(insight)
    used = [sid for st in ans.statements for sid in st.source_ids if sid in index]
    if ans.out_of_scope:
        return []
    picked = list(dict.fromkeys(used)) or list(index)
    return [index[sid] for sid in picked]
