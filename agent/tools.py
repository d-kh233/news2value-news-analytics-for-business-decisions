"""Инструменты агента.

get_relevant_news(company_id, period) — сигнатура из ТЗ. Источник новостей подключается
функцией news_provider (бэкенд передаёт свою, когда появится таблица новостей); без неё —
заглушка agent/data/mock_news.json, и это видно в трейсе.

search_bank_analytics — локальный BM25 по тезисам аналитика (с учётом ОКВЭД) (agent/data/bank_theses.json),
пока у бэкенда нет индекса. Подключается так же: bank_provider(query, k) -> [chunk].
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from typing import Callable, Optional

from .config import DATA_DIR
from .retrieval import BM25
from .schemas import BankThesis, NewsItem

NewsProvider = Callable[[str, int], list[dict]]


@lru_cache(maxsize=1)
def load_bank_theses() -> tuple[BankThesis, ...]:
    data = json.loads((DATA_DIR / "bank_theses.json").read_text(encoding="utf-8"))
    return tuple(BankThesis(**d) for d in data)


@lru_cache(maxsize=1)
def _mock_news() -> tuple[NewsItem, ...]:
    data = json.loads((DATA_DIR / "mock_news.json").read_text(encoding="utf-8"))
    return tuple(NewsItem.from_dict(d) for d in data)


class Tools:
    def __init__(self, today: date, news_provider: Optional[NewsProvider] = None):
        self.today = today
        self.news_provider = news_provider
        self.theses = load_bank_theses()
        self._bm25 = BM25([f"{t.title}. {t.thesis}" for t in self.theses])

    def get_relevant_news(self, company_id: str, period: int) -> tuple[list[NewsItem], str]:
        if self.news_provider is not None:
            return [NewsItem.from_dict(d) for d in self.news_provider(company_id, period)], "provider"
        since = self.today - timedelta(days=period)
        return [n.model_copy(deep=True) for n in _mock_news() if since <= n.published_at <= self.today], \
            "mock (источник новостей не подключён)"

    def search_bank_analytics(self, query: str, factors: list[str], k: int = 2,
                              okved2: str | None = None) -> tuple[list[tuple[BankThesis, float]], str]:
        """Тезисы банка к новости. Скор = BM25 (0..1) + 0.6 за каждый общий фактор + 0.8, если тезис
        про отрасль компании. Тезис про чужую отрасль не берём: «дезинфляция в агро» рознице не позиция
        банка. Совсем слабые совпадения (ни фактора, ни отрасли, слабый текст) отсекаем: лучше честное
        «нет аналитики банка по теме», чем позиция банка не про это событие."""
        raw = self._bm25.scores(query)
        mx = max(raw) or 1.0
        scored = []
        for t, s in zip(self.theses, raw):
            lex = s / mx
            overlap = len(set(factors) & set(t.factors))
            same_industry = bool(okved2 and okved2 in t.okved)
            if t.okved and not same_industry:
                continue
            if not overlap and not same_industry and lex < 0.6:
                continue
            scored.append((t, round(lex + 0.6 * overlap + (0.8 if same_industry else 0.0), 3)))
        scored.sort(key=lambda x: -x[1])
        top = scored[0][1] if scored else 0
        # второй тезис берём, только если он почти так же близок, как лучший
        return [x for x in scored if x[1] >= 0.75 * top][:k], "local_bm25"

    def resolve_source(self, news: NewsItem, related: list[NewsItem] = ()):
        """Для llm.backend_adapter: id источника -> {title, url, published_at, source_kind}."""
        known = {n.sid: {"title": n.title, "url": n.url or "", "published_at": n.published_iso,
                         "source_kind": "external"} for n in [news, *related]}
        for t in self.theses:
            known[t.sid] = {"title": t.title, "url": t.url, "published_at": t.published_at,
                            "source_kind": "bank_analytics"}
        return known.get
