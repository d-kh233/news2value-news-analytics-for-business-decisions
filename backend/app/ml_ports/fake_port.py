"""Заглушка вместо агента (ML_MODE=fake): совпадение слов профиля с новостью + бонус за свежесть.

Нужна, когда agent/ и llm/ не слиты или сломаны: refresh и лента работают и без них.
Контракт тот же, что у agent_port.generate_insights: (карточки в схеме Insight, сводка с трейсом).
Пакеты agent/ и llm/ не импортируются.

Новости — по NEWS_SOURCE, как у агента: db — news_provider (таблица documents),
mock — файл agent/data/mock_news.json (читается как JSON, код агента не нужен).

Карточки честно помечены: generated_by = rules (другого значения схема Insight не допускает),
details = None, prompt_version = fake@v1; в таблице insights — generated_by = fake.

Шаги — как у ML: get_relevant_news (новости за период) → совпадение слов с профилем →
generate_insight на каждую отобранную новость. Обе функции — под кэшем llm_cache (CACHE_ENABLED,
services/llm_cache.py). FAKE_DELAY_SECONDS — пауза в каждом generate_insight (имитация ответа
модели), чтобы проверять фоновую работу; из кэша карточка отдаётся без паузы.
"""

import json
import logging
import re
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.ml_ports.agent_port import ROLES, period_to_days
from app.ml_ports.news_port import agent_today, get_relevant_news
from app.schemas.insight import Insight
from app.services.llm_cache import cached

logger = logging.getLogger(__name__)

PROMPT_VERSION = "fake@v1"
DEFAULT_ROLE = "ceo"  # как у агента (agent/roles.py)
TOP_K = 5
STEM_LEN = 5  # слова сравниваются по первым 5 буквам: «напитки» и «напитков» совпадут
MIN_WORD_LEN = 4
TITLE_WEIGHT = 2  # совпадение в заголовке весит больше, чем в тексте
MIN_MATCH = 2  # меньше (одно слово только в тексте) — случайное совпадение, не связь
FULL_MATCH = 3  # столько совпадений (с весом) — уже полная связь с профилем
FRESH_SHARE = 0.25  # доля свежести в скоре

# общие слова из названий ОКВЭД (основы): в новостях встречаются везде и об отрасли не говорят
_STOP = {
    "деяте", "прочи", "проче", "кроме", "включ", "други", "услуг", "облас", "видов", "предо",
    "разме", "данны", "связа", "этим", "инфор", "оказа", "сети", "средс", "основ", "назна",
    "целей", "работ", "произ", "специ", "неспе", "общег", "совме", "проч",
}  # fmt: skip

# поле профиля -> id признака (как F:<КОЛОНКА> у агента)
_PROFILE_FIELDS = {
    "mainokved_name": "F:MAINOKVED_NAME",
    "industry": "F:INDUSTRY",
    "raw_materials": "F:RAW_MATERIALS",
    "key_clients": "F:KEY_CLIENTS",
    "key_suppliers": "F:KEY_SUPPLIERS",
    "strategic_goals": "F:STRATEGIC_GOALS",
    "currency_risks": "F:CURRENCY_RISKS",
    "fts_country_name": "F:FTS_COUNTRY_NAME",
}


def _stems(text: str) -> set[str]:
    words = re.findall(r"[а-яёa-z]+", (text or "").lower().replace("ё", "е"))
    stems = {w[:STEM_LEN] for w in words if len(w) >= MIN_WORD_LEN}
    return stems - _STOP


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value or "")


def profile_terms(company_id: str) -> dict[str, set[str]]:
    """Основы слов профиля компании из БД: id признака -> основы. Пусто, если компании нет."""
    from app.core.db import get_session
    from app.repositories import company_repo, profile_repo

    with get_session() as session:
        company = company_repo.get_by_id(session, company_id)
        if company is None:
            return {}
        profile = profile_repo.get(session, company_id)
        manual = dict(profile.manual_data) if profile is not None else {}
        values = {
            "mainokved_name": company.mainokved_name,
            "fts_country_name": company.fts_country_name,
            **{k: manual.get(k) for k in _PROFILE_FIELDS if k in manual},
        }
    terms = {_PROFILE_FIELDS[k]: _stems(_as_text(v)) for k, v in values.items()}
    return {fid: stems for fid, stems in terms.items() if stems}


def _mock_news_file() -> Path:
    here = Path(__file__).resolve()
    for root in here.parents[2:4]:
        path = root / "agent" / "data" / "mock_news.json"
        if path.is_file():
            return path
    raise FileNotFoundError("Не найден agent/data/mock_news.json рядом с бэкендом")


def _news(company_id: str, period_days: int, today: date) -> tuple[list[dict[str, Any]], str]:
    """Новости за период по NEWS_SOURCE и название источника для трейса."""
    if get_settings().news_source == "db":
        return get_relevant_news(company_id, period_days), "db"
    since = today - timedelta(days=period_days)
    items = json.loads(_mock_news_file().read_text(encoding="utf-8"))
    window = [n for n in items if since <= date.fromisoformat(n["published_at"][:10]) <= today]
    return window, "mock"


def _first_sentences(text: str, n: int = 2) -> str:
    return " ".join(re.split(r"(?<=[.!?])\s+", (text or "").strip())[:n])


def _score(
    news: dict[str, Any], terms: dict[str, set[str]], today: date, period_days: int
) -> tuple[float, list[str], list[str]]:
    """Скор 0..1, совпавшие основы и признаки профиля, по которым нашлось совпадение."""
    title, body = _stems(news.get("title", "")), _stems(news.get("text", ""))
    matched: set[str] = set()
    fields: list[str] = []
    for fid, stems in terms.items():
        hit = stems & (title | body)
        if hit:
            matched |= hit
            fields.append(fid)
    weight = sum(TITLE_WEIGHT if s in title else 1 for s in matched)
    if weight < MIN_MATCH:
        return 0.0, [], []
    age = (today - date.fromisoformat(news["published_at"][:10])).days
    fresh = max(0.0, 1 - age / max(period_days, 1))
    score = (1 - FRESH_SHARE) * min(1.0, weight / FULL_MATCH) + FRESH_SHARE * fresh
    return round(score, 3), sorted(matched), fields


def _card(
    company_id: str, role: str, news: dict[str, Any], score: float, words: list[str], fields
) -> dict[str, Any]:
    news_id = str(news["id"])
    published = news["published_at"]
    if len(published) == 10:
        published += "T00:00:00Z"
    return {
        "id": f"{company_id}-{news_id}-{role}",
        "company_id": company_id,
        "title": news.get("title") or "Новость по профилю компании",
        "summary": _first_sentences(news.get("text", "")) or news.get("title", ""),
        "type": "event",
        "importance": max(0, min(100, round(score * 100))),
        "impact_on_company": (
            "Новость совпала с профилем компании по словам: "
            f"{', '.join(words)}. Это заглушка (ML_MODE=fake): анализа модели и позиции банка нет."
        ),
        "recommended_action": "Прочитать новость и оценить влияние вручную",
        "sources": [
            {
                "id": f"N:{news_id}",
                "title": news.get("title", ""),
                "url": news.get("url") or "",
                "published_at": published,
                "source_kind": "external",
            }
        ],
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "details": None,
        "used_profile_fields": fields,
        "role": role,
        "generated_by": "rules",
        "prompt_version": PROMPT_VERSION,
    }


@cached("generate_insight", version=lambda: PROMPT_VERSION, validate=Insight.model_validate)
def generate_insight(
    company_id: str,
    role: str,
    news: dict[str, Any],
    score: float,
    words: list[str],
    fields: list[str],
) -> dict[str, Any]:
    """Карточка по одной новости (как llm.insight.generate_insight у ML) и пауза заглушки."""
    delay = get_settings().fake_delay_seconds
    if delay > 0:
        time.sleep(delay)
    return _card(company_id, role, news, score, words, fields)


def _step(n: int, name: str, inp: dict, out: Any, status: str, ms: int, note: str | None) -> dict:
    """Шаг трейса в той же форме, что TraceStep агента."""
    return {
        "step": n,
        "name": name,
        "tool": "fake_port",
        "input": inp,
        "output": out,
        "status": status,
        "duration_ms": ms,
        "note": note,
    }


def generate_insights(
    company_id: str,
    role: str | None = None,
    *,
    period_days: int = 30,
    period: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Карточки по совпадению слов (до 5, сначала с большим скором) и сводка прогона."""
    if period is not None:
        period_days = period_to_days(period)
    role = role or DEFAULT_ROLE
    if role not in ROLES:
        raise ValueError(f"role={role!r}: допустимо {', '.join(ROLES)}")
    today = agent_today()
    t0 = time.perf_counter()
    terms = profile_terms(company_id)
    if not terms:
        raise LookupError(f"Компания {company_id} не найдена или у неё пустой профиль")
    news, source = _news(company_id, period_days, today)
    ms = int((time.perf_counter() - t0) * 1000)
    trace = [
        _step(
            1,
            "Поиск новостей",
            {"company_id": company_id, "period": period_days, "news_source": source},
            {"n": len(news), "ids": [str(n["id"]) for n in news]},
            "ok" if news else "skipped",
            ms,
            None if news else "за период нет новостей",
        )
    ]
    t0 = time.perf_counter()
    scored = [(n, *_score(n, terms, today, period_days)) for n in news]
    ranked = sorted((x for x in scored if x[1] > 0), key=lambda x: -x[1])[:TOP_K]
    cards = [
        generate_insight(company_id, role, n, s, words, fields) for n, s, words, fields in ranked
    ]
    trace.append(
        _step(
            2,
            "Совпадение слов с профилем",
            {"profile_fields": sorted(terms), "top_k": TOP_K},
            [{"news_id": str(n["id"]), "score": s, "words": w} for n, s, w, _ in scored],
            "ok" if cards else "skipped",
            int((time.perf_counter() - t0) * 1000),
            None if cards else "ни одна новость не совпала со словами профиля",
        )
    )
    run_id = f"{company_id}-{role}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    logger.info("fake run %s: %d карточек из %d новостей", run_id, len(cards), len(news))
    summary = {
        "run_id": run_id,
        "role": role,
        "llm_mode": "fake",
        "plan": [
            f"Найти новости за {period_days} дн. (источник {source})",
            "Сравнить слова профиля компании с заголовком и текстом новости",
            f"Отобрать до {TOP_K} новостей с учётом свежести",
        ],
        "trace": trace,
        "llm_usage": {},
    }
    return cards, summary
