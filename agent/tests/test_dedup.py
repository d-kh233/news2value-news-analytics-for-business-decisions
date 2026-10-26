"""Склейка одного события из разных лент и параллельная разметка: python -m unittest agent.tests.test_dedup -v"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent import orchestrator as orch
from agent.config import Settings
from agent.heuristic_extract import heuristic_event
from agent.orchestrator import Orchestrator, same_event
from agent.schemas import NewsItem


def _news(nid: str, title: str, text: str, day: str = "2026-10-24") -> NewsItem:
    n = NewsItem(news_id=nid, title=title, text=text, published_at=date.fromisoformat(day))
    n.event = heuristic_event(n)
    return n


# три пересказа одного решения ЦБ (как в живых лентах 26.10) — тексты почти не совпадают
RATE = [
    _news("r1", "Ставка 14% и ужесточённый прогноз ЦБ",
          "Банк России 23 октября сохранил ключевую ставку на уровне 14% годовых, прогноз средней ставки повышен."),
    _news("r2", "ЦБ оставил ключевую ставку 14% до конца года",
          "Регулятор сохранил ключевую ставку 14%, объяснив решение выбытием производственных мощностей."),
    _news("r3", "Ключевая ставка 14% сохранится — инфляция 6,8%",
          "Текущий рост цен ускорился; Банк России оставил ключевую ставку 14% годовых.", "2026-10-25"),
]
OTHER_RATE = _news("r4", "ЦБ снизил ключевую ставку до 13%", "Банк России снизил ключевую ставку до 13% годовых.",
                   "2026-12-19")
CEMENT = _news("c1", "Цемент подорожал на 14%", "Цены на цемент выросли на 14% с начала осени.")


def _settings(tmp: str, **kw) -> Settings:
    s = Settings()
    s.today = "2026-10-26"
    s.runs_dir = str(Path(tmp) / "runs")
    s.llm_cache_dir = str(Path(tmp) / "cache")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class TestSameEvent(unittest.TestCase):
    def test_retellings_are_one_event(self):
        self.assertTrue(same_event(RATE[0], RATE[1]))
        self.assertTrue(same_event(RATE[0], RATE[2]))

    def test_different_events_kept(self):
        self.assertFalse(same_event(RATE[0], OTHER_RATE))   # другое решение, другая дата и число
        self.assertFalse(same_event(RATE[0], CEMENT))       # то же число 14, но другой тип события

    def test_select_keeps_one_card_per_event(self):
        with tempfile.TemporaryDirectory() as d:
            o = Orchestrator(_settings(d))
            ranked = [(n, SimpleNamespace(score=0.9 - i * 0.05)) for i, n in enumerate(RATE + [CEMENT])]
            chosen, dups = o._select(ranked, 5)
            self.assertEqual([n.news_id for n, _ in chosen], ["r1", "c1"])
            self.assertEqual(dups, {"r2": "r1", "r3": "r1"})


class TestParallelExtract(unittest.TestCase):
    def test_extraction_runs_in_parallel(self):
        active, peak, lock = [0], [0], threading.Lock()

        def fake_extract(news, complete, trace):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.1)
            with lock:
                active[0] -= 1
            return heuristic_event(NewsItem(news_id=news["id"], title=news["title"], text=news["text"],
                                            published_at=date(2026, 10, 24)))

        news = [NewsItem(news_id=f"n{i}", title=f"Новость {i}", text="текст", published_at=date(2026, 10, 24))
                for i in range(8)]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(orch, "extract_event", fake_extract):
            o = Orchestrator(_settings(d, llm_mode="live", llm_parallel=4))
            trace: list = []
            t0 = time.perf_counter()
            o._extract_missing(trace, news, {}, gw=SimpleNamespace(complete=None), llm_trace=[])
            took = time.perf_counter() - t0
        self.assertTrue(all(n.event is not None for n in news))
        self.assertEqual(peak[0], 4)
        self.assertLess(took, 0.5)                          # по очереди было бы 0,8 с
        self.assertEqual(trace[0].output["extracted"], [f"n{i}" for i in range(8)])


if __name__ == "__main__":
    unittest.main()
