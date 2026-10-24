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


COMPANY = {  # как строка из таблицы companies бэкенда (ключи в нижнем регистре)
    "synthetic_u": "U_2", "mainokved_code": "46.34.2", "mainokved_name": "Торговля оптовая напитками",
    "revenue_company": 5000000.0, "region": "Москва", "cl_product_loa_mark": True,
    "cl_product_dep_mark": False, "fts_flag": True, "fts_country_name": "КИТАЙ, ТУРЦИЯ", "group_id": "group_1_sb",
}
GOOD_INSIGHT = {
    "news_id": "N:42", "company_id": "U_2", "headline": "Кредит не подешевеет, а импорт напитков дорожает",
    "impact_type": "risk", "importance": "high", "confidence": "medium",
    "what_happened": [{"text": "Банк России сохранил ставку 14%.", "origin": "fact", "source_ids": ["N:42"]}],
    "why_it_matters": [{"text": "У вас есть кредитные продукты.", "origin": "ai",
                        "source_ids": ["F:CL_PRODUCT_LOA_MARK", "N:42"]}],
    "consequences": [{"text": "Процентные расходы останутся высокими", "kind": "risk", "level": "medium",
                      "horizon": "short", "origin": "bank", "source_ids": ["A:key_rate:1"]}],
    "actions": [{"text": "Сравнить фиксированную и плавающую ставку по действующему кредиту", "type": "question"}],
    "bank_view": [{"text": "Банк ожидает паузы в снижении ставки.", "origin": "bank", "source_ids": ["A:key_rate:1"]}],
    "company_features_used": ["F:CL_PRODUCT_LOA_MARK", "F:FTS_FLAG"],
}
BANK = [{"id": "A:key_rate:1", "title": "Ключевая ставка ЦБ РФ: ставка без изменения на 14,0%",
         "date": "2026-09-12", "published_at": "2026-09-12T09:00:00Z", "text": "Ожидаем паузы."}]


class TestInsight(unittest.TestCase):
    def _event(self):
        from llm.schemas import EventExtraction
        return EventExtraction(**EXAMPLES[0]["answer"], news_id="42")

    def test_context_has_company_and_ids(self):
        from llm.insight import build_context
        msg, allowed, impact = build_context(self._event(), "текст", COMPANY, bank_chunks=BANK, role="cfo")
        self.assertIn("[F:CL_PRODUCT_LOA_MARK] Есть кредитные продукты", msg)
        self.assertIn("[F:FTS_COUNTRY_NAME] Страны ВЭД по данным ФТС (экспорт или импорт — в данных не указано): КИТАЙ, ТУРЦИЯ", msg)
        self.assertIn("G:group_1_sb", allowed)
        self.assertIn("A:key_rate:1", allowed)
        self.assertIn("Финансовый директор", msg)
        self.assertGreater(impact["score"], 0)

    def test_invented_source_triggers_repair(self):
        from llm.insight import interpret
        bad = dict(GOOD_INSIGHT, bank_view=[{"text": "x", "origin": "bank", "source_ids": ["A:invented:9"]}])
        fake = FakeLLM([json.dumps(bad, ensure_ascii=False), json.dumps(GOOD_INSIGHT, ensure_ascii=False)])
        ins, _ = interpret(self._event(), "текст", COMPANY, bank_chunks=BANK, complete=fake, model="fake")
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("несуществующие источники", fake.calls[1][-1]["content"])

    def test_generate_insight_end_to_end(self):
        from llm.insight import generate_insight
        news = {"id": "42", "title": "ЦБ сохранил ставку", "text": "Банк России сохранил ставку 14%.",
                "source": "test", "url": "https://cbr.example", "published_at": "2026-09-11T10:00:00Z"}
        fake = FakeLLM([json.dumps(EXAMPLES[0]["answer"], ensure_ascii=False),
                        json.dumps(GOOD_INSIGHT, ensure_ascii=False)])
        trace = []
        out = generate_insight(news, COMPANY, bank_chunks=BANK, role="cfo", complete=fake, trace=trace)
        self.assertEqual(out["company_id"], "U_2")
        self.assertEqual(len(out["sources"]), 2)
        self.assertEqual(out["role"], "cfo")
        self.assertTrue(any(t.get("step") == "interpret" for t in trace))


class TestExtraCheck(unittest.TestCase):
    def test_extra_check_runs_in_repair_loop(self):
        from llm.insight import interpret
        from llm.schemas import EventExtraction
        ev = EventExtraction(**EXAMPLES[0]["answer"], news_id="42")
        calls = []

        def no_long_headlines(ins):
            calls.append(ins.headline)
            if len(calls) == 1:
                raise ValueError("Заголовок слишком общий")

        fake = FakeLLM([json.dumps(GOOD_INSIGHT, ensure_ascii=False)] * 2)
        interpret(ev, "текст", COMPANY, bank_chunks=BANK, complete=fake, model="fake2", extra_check=no_long_headlines)
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("Заголовок слишком общий", fake.calls[1][-1]["content"])


class TestAnalystMap(unittest.TestCase):
    def test_signed_convention_and_leading_zero(self):
        up = [{"factor": "key_rate", "direction": "up", "strength": "high"}]
        self.assertIn("01", load_map())  # в файле аналитика «1», без нуля
        self.assertEqual(macro_impact(up, {"MAINOKVED_CODE": "64.19"})["sign"], "opportunity")  # банки
        self.assertEqual(macro_impact(up, {"MAINOKVED_CODE": "01.11"})["sign"], "risk")
        sanc = [{"factor": "sanctions_trade", "direction": "up", "strength": "medium"}]
        self.assertEqual(macro_impact(sanc, {"MAINOKVED_CODE": "06.10"})["sign"], "risk")


class TestDedupMoves(unittest.TestCase):
    def test_one_move_per_factor(self):
        ans = dict(EXAMPLES[1]["answer"])
        ans["factor_moves"] = ans["factor_moves"] + [
            {"factor": "metals", "direction": "up", "strength": "high", "evidence": "дефицит"}]
        fake = FakeLLM([json.dumps(ans, ensure_ascii=False)])
        ev = extract_event({"id": "m1", "title": "медь", "text": "медь дорожает x"}, complete=fake, model="fake3")
        self.assertEqual(len(ev.factor_moves), 1)
        self.assertEqual(ev.factor_moves[0].strength.value, "high")


class TestExtractClean(unittest.TestCase):
    def test_dedup_factors_and_country_region(self):
        ans = dict(EXAMPLES[0]["answer"], regions=["Россия", "Подмосковье"], factor_moves=[
            {"factor": "labor_market", "direction": "up", "strength": "medium", "evidence": "a"},
            {"factor": "labor_market", "direction": "up", "strength": "high", "evidence": "b"}])
        fake = FakeLLM([json.dumps(ans, ensure_ascii=False)])
        ev = extract_event({"id": "c1", "title": "t", "text": "уникальный текст для теста чистки"}, complete=fake, model="fake3")
        self.assertEqual(len(ev.factor_moves), 1)
        self.assertEqual(ev.factor_moves[0].strength.value, "high")
        self.assertEqual(ev.regions, ["Подмосковье"])


class TestProfileFields(unittest.TestCase):
    def test_profile_lists_visible(self):
        from llm.insight import company_facts
        f = company_facts({"mainokved_code": "24.10", "raw_materials": ["железная руда", "кокс"],
                           "key_clients": [], "currency_risks": ["CNY"], "manual_data": {"KEY_SUPPLIERS": ["ООО Руда"]}})
        txt = {x["id"]: x["text"] for x in f}
        self.assertEqual(txt["F:RAW_MATERIALS"], "Ключевое сырьё и материалы: железная руда, кокс")
        self.assertIn("F:CURRENCY_RISKS", txt)
        self.assertIn("F:KEY_SUPPLIERS", txt)
        self.assertNotIn("F:KEY_CLIENTS", txt)  # пустой список не показываем


class TestCritic(unittest.TestCase):
    def _event(self):
        from llm.schemas import EventExtraction
        return EventExtraction(**EXAMPLES[0]["answer"], news_id="42")

    def test_pass(self):
        from llm.critic import interpret_reviewed
        ok = {"verdict": "pass", "scores": {"grounding": 5}, "issues": [], "extra_search": []}
        fake = FakeLLM([json.dumps(GOOD_INSIGHT, ensure_ascii=False), json.dumps(ok)])
        ins, _, r = interpret_reviewed(self._event(), "текст критик", COMPANY, bank_chunks=BANK, complete=fake, model="c1")
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(len(fake.calls), 2)

    def test_revise_loop(self):
        from llm.critic import interpret_reviewed
        bad = {"verdict": "revise", "scores": {"depth": 2},
               "issues": [{"field": "headline", "problem": "retelling", "comment": "Пересказ новости"}], "extra_search": []}
        fixed = dict(GOOD_INSIGHT, headline="Кредит не подешевеет до конца года — пересмотрите график")
        fake = FakeLLM([json.dumps(GOOD_INSIGHT, ensure_ascii=False), json.dumps(bad, ensure_ascii=False),
                        json.dumps(fixed, ensure_ascii=False)])
        ins, _, r = interpret_reviewed(self._event(), "текст критик 2", COMPANY, bank_chunks=BANK, complete=fake, model="c2")
        self.assertEqual(r["verdict"], "revised")
        self.assertTrue(ins.headline.startswith("Кредит не подешевеет до конца года"))
        self.assertIn("Пересказ новости", fake.calls[2][-1]["content"])

    def test_soft_critic_overruled(self):
        from llm.critic import critique
        from llm.schemas import Insight
        soft = {"verdict": "pass", "scores": {}, "issues": [
            {"field": "bank_view", "problem": "wrong_origin", "comment": "вывод модели помечен bank"}], "extra_search": []}
        v = critique(Insight.model_validate(GOOD_INSIGHT), "ctx", complete=FakeLLM([json.dumps(soft, ensure_ascii=False)]), model="c3")
        self.assertEqual(v.verdict, "revise")


class TestIncompleteProfile(unittest.TestCase):
    def test_empty_profile_honest(self):
        from llm.insight import interpret
        from llm.schemas import EventExtraction
        ev = EventExtraction(**EXAMPLES[0]["answer"], news_id="42")
        g = dict(GOOD_INSIGHT, confidence="high", why_it_matters=[{"text": "общий", "origin": "ai", "source_ids": ["N:42"]}],
                 company_features_used=[], bank_view=[],
                 consequences=[dict(GOOD_INSIGHT["consequences"][0], origin="ai", source_ids=[])])
        ins, _ = interpret(ev, "пустой профиль", {"SYNTHETIC_U": "R-3"},
                           complete=FakeLLM([json.dumps(g, ensure_ascii=False)]), model="ip1")
        self.assertEqual(ins.confidence.value, "low")
        self.assertTrue(any("ОКВЭД" in x for x in ins.data_gaps))

    def test_okved_not_allowed_when_missing(self):
        from llm.insight import build_context
        from llm.schemas import EventExtraction
        _, allowed, _ = build_context(EventExtraction(**EXAMPLES[0]["answer"], news_id="42"), "t", {"SYNTHETIC_U": "R-3"})
        self.assertNotIn("F:MAINOKVED_CODE", allowed)


class TestFactcheck(unittest.TestCase):
    def test_flags_invented_number_and_stale(self):
        from llm.factcheck import check
        src = {"N:M01": {"text": "ЦБ сохранил ставку 14% годовых", "date": __import__("datetime").date(2026, 10, 20)},
               "A:BA-06": {"text": "Ставка 14,0%. Период дорогих денег продлевается", "date": __import__("datetime").date(2026, 7, 1)}}
        card = {"id": "x", "title": "Ставка 14% надолго",
                "details": {"what_happened": [{"text": "Ставка сохранена на 14%, инфляция 9%", "origin": "fact", "source_ids": ["N:M01"]}],
                            "bank_view": [{"text": "Банк ждёт 14,0% до конца года", "origin": "bank", "source_ids": ["A:BA-06:1"]}],
                            "why_it_matters": [], "consequences": [], "data_gaps": []}}
        issues = check(card, src)
        self.assertTrue(any("['9']" in i for i in issues))
        self.assertTrue(any("старше новости" in i for i in issues))
        self.assertFalse(any("Позиция банка: числа" in i for i in issues))   # 14,0 == 14
