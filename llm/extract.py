"""
Шаг 1 пайплайна: классификация новости и извлечение ключевой информации (Gemma).

Использование:
    from llm.extract import extract_event
    ev = extract_event({"id": "1842", "title": ..., "text": ..., "source": ..., "url": ..., "published_at": ...})
    ev.event_type, ev.factor_moves, ev.is_noise ...

Пачкой:
    events, errors = extract_many(news_list)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from .client import MODELS, CompleteFn, LLMCallError, call_json
from .prompts.extract_event_examples import EXAMPLES
from .schemas import EventExtraction, EventExtractionLLM

PROMPT_VERSION = "extract_event@v2"
SYSTEM = (Path(__file__).parent / "prompts" / "extract_event.system.md").read_text(encoding="utf-8")
MAX_TEXT_CHARS = 6000   # длиннее режем: экономия токенов, суть обычно в начале


def _news_block(news: dict) -> str:
    text = (news.get("text") or "").strip()
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS] + " …[текст обрезан]"
    parts = [
        f"Заголовок: {news.get('title') or '—'}",
        f"Источник: {news.get('source') or '—'}",
        f"Дата публикации: {news.get('published_at') or '—'}",
        "Текст:",
        text or "—",
    ]
    return "\n".join(parts)


def build_messages(news: dict, n_examples: int = 3) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM}]
    for ex in EXAMPLES[:n_examples]:
        msgs.append({"role": "user", "content": _news_block(ex["news"])})
        msgs.append({"role": "assistant", "content": json.dumps(ex["answer"], ensure_ascii=False)})
    msgs.append({"role": "user", "content": _news_block(news)})
    return msgs


def extract_event(news: dict, complete: Optional[CompleteFn] = None, model: Optional[str] = None,
                  trace: Optional[list] = None, n_examples: int = 3) -> EventExtraction:
    model = model or MODELS["fast"]
    llm_out = call_json(EventExtractionLLM, build_messages(news, n_examples), model=model,
                        complete=complete, trace=trace, temperature=0.0, max_tokens=1200)
    ev = EventExtraction(
        **llm_out.model_dump(),
        news_id=str(news["id"]),
        source=news.get("source"),
        url=news.get("url"),
        published_at=news.get("published_at"),
        model=model,
        prompt_version=PROMPT_VERSION,
    )
    # один фактор — одна запись: оставляем самую сильную
    rank = {"low": 0, "medium": 1, "high": 2}
    best: dict = {}
    for fm in ev.factor_moves:
        k = fm.factor
        if k not in best or rank[fm.strength.value] > rank[best[k].strength.value]:
            best[k] = fm
    ev.factor_moves = list(best.values())
    if len((news.get("text") or "")) > MAX_TEXT_CHARS and "truncated" not in ev.quality_flags:
        ev.quality_flags.append("truncated")
    if not ev.event_date and not ev.is_noise and "no_date" not in ev.quality_flags:
        # даты события в тексте нет: дальше используем published_at, но помечаем
        ev.quality_flags.append("no_date")
    return ev


def extract_many(news_list: Iterable[dict], complete: Optional[CompleteFn] = None,
                 model: Optional[str] = None) -> tuple[list[EventExtraction], list[dict]]:
    """Не падает на одной плохой новости: ошибки возвращаются отдельным списком."""
    events, errors = [], []
    for n in news_list:
        try:
            events.append(extract_event(n, complete=complete, model=model))
        except (LLMCallError, OSError) as e:
            errors.append({"news_id": n.get("id"), "error": str(e)[:500]})
    return events, errors


if __name__ == "__main__":
    # Быстрая проверка промпта на своих новостях:
    #   python -m llm.extract news.json      (список объектов {id, title, text, source, url, published_at})
    import sys
    from .client import openai_compatible_complete  # noqa: F401  (нужен .env)

    news = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    events, errors = extract_many(news)
    for e in events:
        print(json.dumps(e.model_dump(mode="json"), ensure_ascii=False, indent=2))
    if errors:
        print("ОШИБКИ:", json.dumps(errors, ensure_ascii=False, indent=2))
