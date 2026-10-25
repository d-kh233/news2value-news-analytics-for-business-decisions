"""Разметка новости без модели: EventExtraction по ключевым словам.

# ПРАВКА feat/llm-fixes-daria: новый модуль. Неразмеченные новости из БД (NEWS_SOURCE=db) в
# LLM_MODE=stub, при промахе replay или сбое модели получали скор 0, и прогон молча давал 0
# карточек. Теперь такие новости размечаются грубо: отрасль — llm.okved.tagging (теги okved:NN),
# макрофакторы — по словарю ниже, сила low. Направление — только если фактор и слово-направление
# («вырос», «снизил», «сохранил») есть в заголовке или первой фразе; иначе uncertain. Это заметно слабее разметки моделью (llm.extract), поэтому:
#   • вызывается только для новостей, которые остались без разметки после extract_event;
#   • в разметке model = "rules", prompt_version = HEURISTIC_VERSION — видно в трейсе и карточке;
#   • происшествия (атаки, погибшие, протесты) помечаются шумом: связь с отраслью по слову
#     «туристы» или «подстанция» — не повод для инсайта.
Когда в replay_cache появятся записи extract_event, эти новости разметит модель и модуль не нужен.
"""
from __future__ import annotations

import re

from llm.okved.tagging import industry_tags, okved_codes, tag_label
from llm.schemas import EventExtraction

from .schemas import NewsItem

HEURISTIC_VERSION = "heuristic_extract@v0"

# фактор -> корни (совпадение с началом слова; несколько слов — подряд)
FACTOR_ROOTS: dict[str, list[str]] = {
    "key_rate": ["ключев ставк", "ставку цб", "ставки цб", "денежно-кредитн", "дкп"],
    "usd_rub": ["курс рубл", "курс доллар", "курс юан", "курс евро", "девальвац", "валютн курс"],
    "oil_gas": ["нефт", "газ", "энергонос", "brent", "urals", "дизтоплив", "бензин"],
    "metals": ["металл", "сталь", "алюмини", "никел", "медь"],
    "agri": ["зерн", "пшениц", "урожа", "сельхозсыр", "продовольств"],
    "gold": ["золот"],
    "inflation": ["инфляц", "дезинфляц", "рост цен", "подорожа"],
    "consumer_demand": ["потребительск спрос", "потребительск активн", "розничн продаж",
                        "кредитов наличн", "потребкредит"],
    "labor_market": ["зарплат", "рынок труда", "рынке труда", "дефицит кадр", "безработ"],
    "sanctions_trade": ["санкц", "пошлин", "эмбарго", "утильсбор", "импорт", "экспорт"],
}
# тип события по фактору: первый подходящий по порядку
FACTOR_EVENT = [("key_rate", "monetary_policy"), ("inflation", "inflation"), ("usd_rub", "fx"),
                ("oil_gas", "commodities"), ("metals", "commodities"), ("agri", "commodities"),
                ("gold", "commodities")]
REGULATION_ROOTS = ["закон", "госдум", "налог", "минфин", "минпромторг", "правительств", "регулир",
                    "льгот", "спецрежим"]
# происшествия и политика без экономического содержания: модель пометила бы их is_noise
NOISE_ROOTS = ["погиб", "пострада", "бпла", "беспилот", "атак", "застрел", "убит", "ранен", "спецназ",
               "протест", "столкновен", "сорвал", "утонул", "опрокин", "пожар", "дтп", "теракт", "подтоп",
               "затоп"]
# направление движения фактора по словам заголовка и первой фразы
SAME_ROOTS = ["сохрани", "сохранен", "пауз", "без изменен", "не спешит"]
UP_ROOTS = ["вырос", "рост", "повыс", "повыш", "подорож", "ускор", "увелич"]
DOWN_ROOTS = ["снизи", "сниж", "снизя", "упал", "паден", "подешев", "замедл", "сократ", "уменьш",
              "дезинфляц"]
LEAD_CHARS = 1500

_WORD = re.compile(r"[а-яёa-z0-9]+(?:-[а-яёa-z0-9]+)?")


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("ё", "е"))


def _has(roots: list[str], words: list[str]) -> str | None:
    """Первый совпавший корень (для evidence) или None."""
    for root in roots:
        parts = root.split()
        n = len(parts)
        for i in range(len(words) - n + 1):
            if all((words[i + j] == p) if len(p) <= 3 else words[i + j].startswith(p)
                   for j, p in enumerate(parts)):
                return " ".join(words[i:i + n])
    return None


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return parts[0][:300] if parts and parts[0] else ""


def _direction(words: list[str]) -> str:
    """unchanged | up | down | uncertain по словам заголовка и первой фразы."""
    if _has(SAME_ROOTS, words):
        return "unchanged"
    up, down = _has(UP_ROOTS, words), _has(DOWN_ROOTS, words)
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    return "uncertain"


def heuristic_event(news: NewsItem) -> EventExtraction:
    """Грубая разметка одной новости (без вызова модели)."""
    head = _words(news.title)
    lead = head + _words(_first_sentence(news.text))
    words = head + _words(news.text[:LEAD_CHARS])
    tags = industry_tags(news.title, news.text)
    codes = okved_codes(tags)
    lead_dir = _direction(lead)

    moves, found = [], {}
    for factor, roots in FACTOR_ROOTS.items():
        hit = _has(roots, words)
        if hit:
            found[factor] = hit
            # направлению верим, только если фактор назван там же, где слово-направление
            direction = lead_dir if _has(roots, lead) else "uncertain"
            moves.append({"factor": factor, "direction": direction, "strength": "low",
                          "evidence": f"в тексте: «{hit}» (разметка по ключевым словам)"})

    noise = bool(_has(NOISE_ROOTS, head)) and not moves
    event_type = next((et for f, et in FACTOR_EVENT if f in found), None)
    if event_type is None and "sanctions_trade" in found:
        event_type = "sanctions" if found["sanctions_trade"].startswith("санкц") else "trade"
    if event_type is None and _has(REGULATION_ROOTS, words):
        event_type = "regulation"
    if event_type is None:
        event_type = "industry" if codes else "other"

    return EventExtraction(
        news_id=news.news_id, title=news.title[:200], summary=_first_sentence(news.text) or news.title,
        event_type=event_type, okved_hints=codes, industries=[tag_label(t) for t in tags],
        factor_moves=moves, is_noise=noise, quality_flags=["ambiguous"],
        source=news.source, url=news.url, published_at=news.published_at.isoformat(),
        model="rules", prompt_version=HEURISTIC_VERSION,
    )
