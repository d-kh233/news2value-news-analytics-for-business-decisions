"""Разметка без модели и пустой отбор с объяснением: python -m unittest agent.tests.test_heuristic -v

# ПРАВКА feat/llm-fixes-daria: новые тесты к agent/heuristic_extract.py и правкам orchestrator.py.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from agent.config import Settings
from agent.heuristic_extract import HEURISTIC_VERSION, heuristic_event
from agent.orchestrator import Orchestrator
from agent.schemas import CompanyProfile, NewsItem

TODAY = "2026-10-24"
NEWS = [
    {"id": "doc-1", "title": "Застройщики просят продлить льготную ипотеку",
     "text": "Девелоперы жилья предупреждают о падении продаж новостроек, если льготную ипотеку не продлят. "
             "Высокая ключевая ставка делает рыночную ипотеку недоступной.",
     "published_at": "2026-10-23T09:00:00Z", "url": "https://example.org/1", "source": "test"},
    {"id": "doc-2", "title": "Двое туристов сорвались в районе водопадов",
     "text": "Туристы пострадали во время похода, их доставили в больницу.",
     "published_at": "2026-10-23T10:00:00Z", "url": "https://example.org/2", "source": "test"},
    {"id": "doc-3", "title": "Даниил Медведев стал финалистом турнира",
     "text": "Теннисист вышел в финал турнира.", "published_at": "2026-10-23T11:00:00Z", "source": "test"},
]
BUILDER = {"SYNTHETIC_U": "B1", "MAINOKVED_CODE": "41.20", "MAINOKVED_NAME": "Строительство жилых зданий",
           "REGION": "Москва", "CL_PRODUCT_LOA_MARK": "1"}


def _settings(tmp: str) -> Settings:
    s = Settings()
    s.today = TODAY
    s.runs_dir = str(Path(tmp) / "runs")
    s.llm_cache_dir = str(Path(tmp) / "cache")
    return s


def _item(d: dict) -> NewsItem:
    return NewsItem.from_dict(d)


class TestHeuristicEvent(unittest.TestCase):
    def test_industry_and_factors(self):
        ev = heuristic_event(_item(NEWS[0]))
        self.assertIn("41", ev.okved_hints)
        self.assertEqual({m.factor.value for m in ev.factor_moves} >= {"key_rate"}, True)
        self.assertTrue(all(m.direction.value == "uncertain" for m in ev.factor_moves))
        self.assertEqual(ev.event_type.value, "monetary_policy")
        self.assertFalse(ev.is_noise)
        self.assertEqual((ev.model, ev.prompt_version), ("rules", HEURISTIC_VERSION))

    def test_direction_only_from_title_and_lead(self):
        rate = heuristic_event(NewsItem(news_id="r", title="ЦБ сохранил ключевую ставку на уровне 14%",
                                        text="Решение ожидаемое.", published_at=date(2026, 10, 23)))
        self.assertEqual([(m.factor.value, m.direction.value) for m in rate.factor_moves], [("key_rate", "unchanged")])
        prices = heuristic_event(NewsItem(news_id="p", title="Рост цен ускорился до 8%",
                                          text="Инфляция выше прогноза. Позже ключевая ставка может вырасти.",
                                          published_at=date(2026, 10, 23)))
        moves = {m.factor.value: m.direction.value for m in prices.factor_moves}
        self.assertEqual(moves["inflation"], "up")
        self.assertEqual(moves["key_rate"], "uncertain")   # ставка — не в заголовке и не в первой фразе

    def test_incident_is_noise(self):
        self.assertTrue(heuristic_event(_item(NEWS[1])).is_noise)

    def test_unrelated_is_other(self):
        ev = heuristic_event(NewsItem(news_id="x", title="Прошла выставка кошек", text="Кошки.",
                                      published_at=date(2026, 10, 23)))
        self.assertEqual((ev.event_type.value, ev.okved_hints, ev.factor_moves), ("other", [], []))


class TestStubWithUnlabeledNews(unittest.TestCase):
    def test_builder_gets_card_from_db_like_news(self):
        """Раньше: неразмеченные новости в stub — скор 0 и 0 карточек. Теперь карточка по ипотеке есть."""
        with tempfile.TemporaryDirectory() as d:
            o = Orchestrator(_settings(d), news_provider=lambda cid, period: [dict(n) for n in NEWS])
            r = o.run(CompanyProfile.from_row(BUILDER), 7)
            ids = [c["id"] for c in r.insights]
            self.assertIn("B1-doc-1-ceo", ids)
            self.assertNotIn("B1-doc-2-ceo", ids)       # происшествие — шум
            step = next(s for s in r.trace if s.tool == "heuristic_extract")
            self.assertEqual(step.status, "fallback")
            self.assertEqual(len(step.output), 3)
            card = next(c for c in r.insights if c["id"] == "B1-doc-1-ceo")
            self.assertIn("N:doc-1", [s["id"] for s in card["sources"]])   # расхождение 2: id источника

    def test_empty_selection_explained(self):
        """Ни одна новость не прошла порог — в трейсе числа, а не одна фраза."""
        with tempfile.TemporaryDirectory() as d:
            o = Orchestrator(_settings(d), news_provider=lambda cid, period: [dict(NEWS[2])])
            r = o.run(CompanyProfile.from_row(BUILDER), 7)
            self.assertEqual(r.insights, [])
            step = next(s for s in r.trace if s.tool == "score_news")
            self.assertEqual(step.status, "skipped")
            self.assertIn("по ключевым словам 1", step.note)
            self.assertIn("лучший скор", step.note)

    def test_window_expansion_visible(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []

            def provider(cid, period):
                calls.append(period)
                return [dict(NEWS[0])] if period >= 4 else []

            r = Orchestrator(_settings(d), news_provider=provider).run(CompanyProfile.from_row(BUILDER), 1)
            self.assertEqual(calls, [1, 2, 4])
            last = [s for s in r.trace if s.tool == "get_relevant_news"][-1]
            self.assertEqual(last.status, "fallback")
            self.assertIn("окно расширено до 4 дн.", last.note)


class TestCounterparties(unittest.TestCase):
    def test_counterparties_from_row(self):
        from agent.generator import counterparties_of
        row = dict(BUILDER, COUNTERPARTIES=[{"id": "P:U_3", "direction": "покупатель", "okved_name": "Торговля",
                                             "share_pct": 40.0}, {"bad": 1}])
        self.assertEqual(counterparties_of(CompanyProfile.from_row(row)),
                         [{"id": "P:U_3", "direction": "покупатель", "okved_name": "Торговля", "share_pct": 40.0}])


if __name__ == "__main__":
    unittest.main()
