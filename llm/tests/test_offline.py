"""
Тесты без обращения к API (не тратят лимит).
Запуск: python -m unittest llm.tests.test_offline -v
"""
import json
import os
import tempfile
import unittest

os.environ["LLM_CACHE_DIR"] = tempfile.mkdtemp()

from llm import client  # noqa: E402
from llm.client import LLMCallError, call_json, extract_json  # noqa: E402
from llm.extract import build_messages, extract_event  # noqa: E402
from llm.okved.sensitivity import load_map, macro_impact  # noqa: E402
from llm.prompts.extract_event_examples import EXAMPLES  # noqa: E402
from llm.schemas import EventExtractionLLM, Insight  # noqa: E402


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, model, messages, **params):
        self.calls.append(messages)
        return self.replies.pop(0)


class TestSchemas(unittest.TestCase):
    def test_examples_match_schema(self):
        for ex in EXAMPLES:
            EventExtractionLLM.model_validate(ex["answer"])

    def test_bank_statement_needs_source(self):
        bad = {
            "news_id": "1", "company_id": "U1", "headline": "x", "impact_type": "risk",
            "importance": "high", "confidence": "medium",
            "what_happened": [{"text": "a", "origin": "fact", "source_ids": ["N:1"]}],
            "why_it_matters": [{"text": "b", "origin": "ai"}],
            "consequences": [{"text": "c", "kind": "risk", "level": "high", "horizon": "short", "origin": "ai"}],
            "bank_view": [{"text": "банк считает", "origin": "bank", "source_ids": []}],
        }
        with self.assertRaises(Exception):
            Insight.model_validate(bad)
        bad["bank_view"][0]["source_ids"] = ["A:key_rate:2"]
        Insight.model_validate(bad)


class TestParsing(unittest.TestCase):
    def test_messy_outputs(self):
        self.assertEqual(extract_json('<think>хм {x}</think>\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Вот ответ: {"a": {"b": "}"}} спасибо'), {"a": {"b": "}"}})
        with self.assertRaises(ValueError):
            extract_json('{"a": 1')


class TestExtract(unittest.TestCase):
    news = {"id": "42", "title": "ЦБ сохранил ставку", "text": "Банк России сохранил ставку 14%.",
            "source": "test", "published_at": "2026-09-11"}

    def test_messages_have_fewshot(self):
        msgs = build_messages(self.news)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(len(msgs), 1 + 2 * len(EXAMPLES) + 1)
        self.assertIn("ЦБ сохранил ставку", msgs[-1]["content"])

    def test_repair_after_invalid_json(self):
        good = json.dumps(EXAMPLES[0]["answer"], ensure_ascii=False)
        fake = FakeLLM(['{"event_type": "нет такого"}', good])
        ev = extract_event(self.news, complete=fake, model="fake")
        self.assertEqual(ev.news_id, "42")
        self.assertEqual(ev.event_type.value, "monetary_policy")
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("Ошибка", fake.calls[1][-1]["content"])

    def test_gives_up(self):
        fake = FakeLLM(["мусор", "опять мусор"])
        with self.assertRaises(LLMCallError):
            call_json(EventExtractionLLM, [{"role": "user", "content": "x2"}], model="fake", complete=fake)


class TestOkved(unittest.TestCase):
    def test_map_complete(self):
        m = load_map()
        self.assertGreaterEqual(len(m), 85)
        self.assertIn("24", m)

    def test_rate_hike_hits_indebted_builder(self):
        moves = [{"factor": "key_rate", "direction": "up", "strength": "high"}]
        builder = {"MAINOKVED_CODE": "41.20", "CL_PRODUCT_LOA_MARK": 1}
        lawyer = {"MAINOKVED_CODE": "69.10"}
        a, b = macro_impact(moves, builder), macro_impact(moves, lawyer)
        self.assertEqual(a["sign"], "risk")
        self.assertGreater(a["score"], b["score"])
        self.assertIn("F:CL_PRODUCT_LOA_MARK", a["why"][0]["source_ids"])

    def test_weak_ruble_exporter_vs_importer(self):
        moves = [{"factor": "usd_rub", "direction": "up", "strength": "medium"}]
        self.assertEqual(macro_impact(moves, {"MAINOKVED_CODE": "24.10", "FTS_FLAG": 1})["sign"], "opportunity")
        self.assertEqual(macro_impact(moves, {"MAINOKVED_CODE": "46.90", "FTS_FLAG": 1})["sign"], "risk")

    def test_unchanged_rate_still_relevant(self):
        moves = [{"factor": "key_rate", "direction": "unchanged", "strength": "high"}]
        r = macro_impact(moves, {"MAINOKVED_CODE": "68.20", "CL_PRODUCT_LOA_MARK": "1"})
        self.assertGreater(r["score"], 0)
        self.assertEqual(r["sign"], "neutral")

    def test_unknown_okved(self):
        self.assertEqual(macro_impact([], {"MAINOKVED_CODE": None})["score"], 0)


if __name__ == "__main__":
    unittest.main()


class TestBackendAdapter(unittest.TestCase):
    def test_maps_to_backend_schema(self):
        from llm.backend_adapter import to_backend_insight
        ins = Insight.model_validate({
            "news_id": "N:1", "company_id": "U_2", "headline": "Деньги остаются дорогими дольше",
            "impact_type": "risk", "importance": "high", "confidence": "medium",
            "what_happened": [{"text": "ЦБ сохранил ставку 14%.", "origin": "fact", "source_ids": ["N:1"]}],
            "why_it_matters": [{"text": "У компании кредит, рефинансирование не подешевеет.", "origin": "ai",
                                "source_ids": ["F:CL_PRODUCT_LOA_MARK"]}],
            "consequences": [{"text": "Процентные расходы не снизятся", "kind": "risk", "level": "medium",
                              "horizon": "short", "origin": "ai"}],
            "actions": [{"text": "Проверить график погашения", "type": "action"}],
            "bank_view": [{"text": "Банк ждёт паузы до конца года.", "origin": "bank", "source_ids": ["A:key_rate:2"]}],
            "company_features_used": ["F:MAINOKVED_CODE"],
        })
        docs = {"N:1": {"title": "ЦБ сохранил ставку", "url": "https://x", "published_at": "2026-09-11T10:00:00Z",
                        "source_kind": "external"},
                "A:key_rate:2": {"title": "Ключевая ставка ЦБ РФ", "url": "https://y",
                                 "published_at": "2026-09-12T10:00:00Z", "source_kind": "bank_analytics"}}
        out = to_backend_insight(ins, "i-1", docs.get, relevance_score=90)
        self.assertEqual(out["type"], "risk")
        self.assertEqual({s["source_kind"] for s in out["sources"]}, {"external", "bank_analytics"})
        self.assertIn("Позиция банка", out["impact_on_company"])
        self.assertEqual(out["used_profile_fields"], ["F:CL_PRODUCT_LOA_MARK", "F:MAINOKVED_CODE"])
        # копия схемы бэкенда (feat/backend, app/schemas/insight.py)
        from typing import Literal
        from pydantic import AwareDatetime, BaseModel, Field

        class Source(BaseModel):
            title: str
            url: str
            published_at: AwareDatetime
            source_kind: Literal["external", "bank_analytics"]

        class BackendInsight(BaseModel):
            id: str
            company_id: str
            title: str
            summary: str
            type: Literal["risk", "opportunity", "event"]
            importance: int = Field(ge=0, le=100)
            impact_on_company: str
            recommended_action: str
            sources: list[Source]
            created_at: AwareDatetime

        BackendInsight.model_validate(out)
