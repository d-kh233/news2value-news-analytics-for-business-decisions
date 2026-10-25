"""
Тесты generate_digest, answer_question и разметки отраслей без обращения к API.
Запуск: python -m unittest llm.tests.test_digest_qa -v

# ПРАВКА feat/llm-fixes-daria: новые тесты к llm/digest.py, llm/qa.py, llm/okved/tagging.py.
"""
import json
import os
import tempfile
import unittest

os.environ["LLM_CACHE_DIR"] = tempfile.mkdtemp()

from llm import client  # noqa: E402
from llm.client import LLMCallError  # noqa: E402
from llm.digest import generate_digest  # noqa: E402
from llm.okved.tagging import industry_tags, merge_tags, okved_codes, tag_label  # noqa: E402
from llm.qa import answer_question, answer_sources, source_index  # noqa: E402


class FakeLLM:
    def __init__(self, replies):
        self.replies = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in replies]
        self.calls = []

    def __call__(self, model, messages, **params):
        self.calls.append(messages)
        return self.replies.pop(0)


INSIGHTS = [
    {"id": "U_1-doc-5-ceo", "type": "risk", "importance": 85, "title": "Кредит не подешевеет до весны",
     "summary": "ЦБ сохранил ставку 14%.", "impact_on_company": "У компании есть кредиты."},
    {"id": "U_1-doc-7-ceo", "type": "opportunity", "importance": 60, "title": "Спрос на жильё поддержат",
     "summary": "Льготную ипотеку продлят.", "impact_on_company": "Компания строит жильё."},
]
COMPANY = {"SYNTHETIC_U": "U_1", "MAINOKVED_CODE": "41.20", "MAINOKVED_NAME": "Строительство жилых зданий"}
DIGEST_OK = {"title": "Неделя: дорогой кредит и поддержка спроса", "overview": "Ставка держит кредит дорогим.",
             "items": [{"news_id": "I:U_1-doc-5-ceo", "line": "Кредит дорог", "impact_type": "risk", "importance": "high"},
                       {"news_id": "I:U_1-doc-7-ceo", "line": "Спрос поддержат", "impact_type": "opportunity",
                        "importance": "medium"}]}

INSIGHT = {
    "id": "U_1-doc-5-ceo", "title": "Кредит не подешевеет до весны", "summary": "ЦБ сохранил ставку 14%.",
    "impact_on_company": "У компании есть кредиты.", "recommended_action": "Проверить график погашений.",
    "sources": [{"id": "N:doc-5", "title": "ЦБ сохранил ставку", "url": "https://e.org/5",
                 "published_at": "2026-10-24T00:00:00Z", "source_kind": "external"},
                {"id": "A:BA-01:0", "title": "Обзор ставки", "url": "", "published_at": "2026-10-01T00:00:00Z",
                 "source_kind": "bank_analytics"}],
    "details": {"what_happened": [{"text": "ЦБ сохранил ставку 14%", "origin": "fact", "source_ids": ["N:doc-5"]}],
                "why_it_matters": [{"text": "есть кредиты", "origin": "ai", "source_ids": ["F:CL_PRODUCT_LOA_MARK"]}],
                "company_features_used": ["F:CL_PRODUCT_LOA_MARK"]},
}
ANSWER_OK = {"answer": "Да, ставка останется высокой: стоит проверить график погашений.",
             "statements": [{"text": "ЦБ сохранил ставку 14%", "origin": "fact", "source_ids": ["N:doc-5"]},
                            {"text": "кредит компании не подешевеет", "origin": "ai",
                             "source_ids": ["F:CL_PRODUCT_LOA_MARK"]}],
             "out_of_scope": False, "suggested_questions": ["Когда ждать снижения?"]}


class TestDigest(unittest.TestCase):
    def setUp(self):
        client.CACHE_DIR = __import__("pathlib").Path(tempfile.mkdtemp())

    def test_digest_ok(self):
        fake = FakeLLM([DIGEST_OK])
        d = generate_digest(INSIGHTS, COMPANY, "week", complete=fake)
        self.assertEqual(d.overview, "Ставка держит кредит дорогим.")
        user = fake.calls[0][-1]["content"]
        self.assertIn("[I:U_1-doc-5-ceo] риск, важность 85", user)
        self.assertIn("неделя", user)
        self.assertIn("[F:MAINOKVED_CODE]", user)

    def test_missing_item_triggers_repair(self):
        bad = dict(DIGEST_OK, items=DIGEST_OK["items"][:1])
        fake = FakeLLM([bad, DIGEST_OK])
        trace = []
        generate_digest(INSIGHTS, COMPANY, "day", complete=fake, trace=trace)
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("нет пунктов для инсайтов", fake.calls[1][-1]["content"])
        self.assertEqual(trace[-1]["prompt_version"], "digest@v0")

    def test_invented_id_gives_up(self):
        bad = dict(DIGEST_OK, items=[dict(DIGEST_OK["items"][0], news_id="I:чужой")])
        with self.assertRaises(LLMCallError):
            generate_digest(INSIGHTS, COMPANY, "day", complete=FakeLLM([bad, bad]))

    def test_empty_not_called(self):
        with self.assertRaises(ValueError):
            generate_digest([], COMPANY, "day", complete=FakeLLM([]))


class TestQA(unittest.TestCase):
    def setUp(self):
        client.CACHE_DIR = __import__("pathlib").Path(tempfile.mkdtemp())

    def test_answer_and_sources(self):
        fake = FakeLLM([ANSWER_OK])
        history = [{"question": "Что случилось?", "answer": "ЦБ сохранил ставку."}]
        a = answer_question(INSIGHT, "Подешевеет ли кредит?", history, company_profile=COMPANY, complete=fake)
        self.assertFalse(a.out_of_scope)
        user = fake.calls[0][-1]["content"]
        self.assertIn("Подешевеет ли кредит?", user)
        self.assertIn("Ответ: ЦБ сохранил ставку.", user)
        self.assertIn("(факт) ЦБ сохранил ставку 14% [N:doc-5]", user)
        self.assertEqual([s["id"] for s in answer_sources(INSIGHT, a)], ["N:doc-5"])

    def test_invented_source_triggers_repair(self):
        bad = dict(ANSWER_OK, statements=[{"text": "x", "origin": "fact", "source_ids": ["N:999"]}])
        fake = FakeLLM([bad, ANSWER_OK])
        answer_question(INSIGHT, "Вопрос?", complete=fake)
        self.assertIn("N:999", fake.calls[1][-1]["content"])

    def test_out_of_scope_has_no_sources(self):
        a = answer_question(INSIGHT, "Какая погода?", complete=FakeLLM([
            {"answer": "Отвечаю только по этому разбору.", "statements": [], "out_of_scope": True}]))
        self.assertEqual(answer_sources(INSIGHT, a), [])

    def test_mock_insight_without_ids(self):
        mock = {k: v for k, v in INSIGHT.items() if k != "details"}
        mock["sources"] = [{k: v for k, v in s.items() if k != "id"} for s in INSIGHT["sources"]]
        self.assertEqual(list(source_index(mock)), ["S:1", "S:2"])
        a = answer_question(mock, "Что делать?", complete=FakeLLM([dict(ANSWER_OK, statements=[])]))
        self.assertEqual(len(answer_sources(mock, a)), 2)   # ссылок нет — все источники инсайта

    def test_empty_question(self):
        with self.assertRaises(ValueError):
            answer_question(INSIGHT, "   ", complete=FakeLLM([]))


class TestIndustryTags(unittest.TestCase):
    def test_real_titles(self):
        self.assertEqual(industry_tags("Набиуллина выступила против увеличения объемов льготной ипотеки",
                                       "Глава ЦБ считает, что льготная ипотека разгоняет цены на жилье."), ["okved:41"])
        self.assertEqual(industry_tags("Аэропорт Сочи вернулся к штатной работе", ""), ["okved:51"])
        self.assertIn("okved:19", industry_tags("Ограничения на отпуск топлива в Мордовии приостановят", ""))

    def test_traps(self):
        # «уголовное» — не уголь, «стал» — не сталь, «почти» — не почта, «Банк России» — не банки
        self.assertEqual(industry_tags("Возбуждено уголовное дело", "Он стал фигурантом, почти признал вину."), [])
        self.assertEqual(industry_tags("ЦБ сохранил ключевую ставку на уровне 14%", "Банк России сохранил ставку."), [])
        self.assertEqual(industry_tags("Газета вышла с опозданием", ""), [])

    def test_merge_and_labels(self):
        self.assertEqual(merge_tags(["economy", "okved:01"], ["okved:41"]), ["economy", "okved:41"])
        self.assertEqual(okved_codes(["economy", "okved:41"]), ["41"])
        self.assertEqual(tag_label("okved:41"), "Строительство зданий")


if __name__ == "__main__":
    unittest.main()
