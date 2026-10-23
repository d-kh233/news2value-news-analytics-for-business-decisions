"""Тесты агента без сети и без ключа: python -m unittest agent.tests.test_agent -v

Поддельный OpenAI-совместимый сервер играет модель: размечает новость примером промпт-инженера,
а на интерпретацию сначала отвечает с выдуманным числом, чужим тезисом и чужим признаком.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from llm.prompts.extract_event_examples import EXAMPLES

from agent.config import Settings
from agent.orchestrator import CompanyNotFound, Orchestrator
from agent.profiles import ProfileStore, detect_csv_kind, read_csv
from agent.schemas import CompanyProfile
from agent.tools import load_bank_theses

logging.getLogger("llm").setLevel(logging.ERROR)   # предупреждения call_json об исправлении — ожидаемые
TODAY = "2026-10-22"
BACKEND_KEYS = {"id", "company_id", "title", "summary", "type", "importance", "impact_on_company",
                "recommended_action", "sources", "created_at"}
UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
RAW_NEWS = [{"id": "B1", "title": "ЦБ сохранил ставку 14%",
             "text": "Банк России сохранил ключевую ставку на уровне 14% годовых. Регулятор допустил более долгое сохранение жёстких условий.",
             "date": "2026-10-20", "link": "https://example.org/b1", "source": "тест"}]


def _settings(tmp: str, **kw) -> Settings:
    s = Settings()
    s.today = TODAY
    s.runs_dir = str(Path(tmp) / "runs")
    s.llm_cache_dir = str(Path(tmp) / "cache")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class FakeModel(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeModel.calls.append(body)
        msgs = body["messages"]
        if "размечает новости" in msgs[0]["content"]:
            reply = EXAMPLES[0]["answer"]                       # разметка: пример про ставку 14%
        else:
            user = msgs[-1]["content"] if len(msgs) == 2 else msgs[1]["content"]
            allowed = user.split("## ДОПУСТИМЫЕ ID ИСТОЧНИКОВ\n", 1)[1].split("\n", 1)[0].split(", ")
            news = next(i for i in allowed if i.startswith("N:"))
            feats = [i for i in allowed if i.startswith("F:")][:2]
            bank = [i for i in allowed if i.startswith("A:")][:1]
            first = len(msgs) == 2
            reply = {
                "news_id": news, "company_id": "MOCK-002",
                "headline": "Кредит для вашей компании не подешевеет до конца года",
                "impact_type": "risk", "importance": "high", "confidence": "medium",
                "what_happened": [{"text": "Ставка выросла до 25%" if first else "Ставка сохранена на уровне 14%",
                                   "origin": "fact", "source_ids": [news]}],
                "why_it_matters": [{"text": "У компании есть кредитные продукты", "origin": "ai",
                                    "source_ids": feats + (["F:ВЫДУМКА"] if first else [])}],
                "consequences": [{"text": "Рефинансирование отложится", "kind": "risk", "level": "high",
                                  "horizon": "short", "origin": "ai", "source_ids": [news]}],
                "actions": [{"text": "Проверить ковенанты", "type": "action", "bank_product": "кредитование",
                             "source_ids": [news]}],
                "bank_view": [{"text": "Банк считает, что дорогие деньги надолго", "origin": "bank",
                               "source_ids": bank}],
                "company_features_used": feats,
                "data_gaps": [],
            }
        content = "```json\n" + json.dumps(reply, ensure_ascii=False) + "\n```"
        out = json.dumps({"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 1000}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestProfiles(unittest.TestCase):
    def test_real_column_format(self):
        st = ProfileStore(Settings().companies_csv)
        p = st.get("MOCK-003")                      # в CSV id с пробелами вокруг
        self.assertEqual(p.okved2, "47")
        self.assertEqual(p.row["REVENUE_COMPANY"], "95000.0")
        facts = p.facts()
        self.assertIn("F:CL_PRODUCT_DEP_MARK", facts)
        self.assertIn("F:CL_PRODUCT_LOA_MARK", facts)   # «нет кредитов» модели тоже показываем
        self.assertEqual(st.get("MOCK-004").industry, "Металлургия")

    def test_from_db_row_lowercase_and_manual_data(self):
        p = CompanyProfile.from_row({"synthetic_u": "U_7", "mainokved_code": "46.34.2", "fts_flag": True,
                                     "region": "Москва", "manual_data": {"FTS_COUNTRY_NAME": "КИТАЙ"}})
        self.assertEqual((p.company_id, p.okved2), ("U_7", "46"))
        self.assertIn("F:FTS_COUNTRY_NAME", p.facts())

    def test_file_kind_detection(self):
        with tempfile.TemporaryDirectory() as d:
            pay, links = Path(d, "a.csv"), Path(d, "b.csv")
            pay.write_text("PAYER;BENEFICIARY;PAY_AMT\n1;2;100\n", encoding="utf-8")
            links.write_text("SYNTHETIC_F;SYNTHETIC_U;CITY_NAME\n1;U_1;Казань\n", encoding="cp1251")
            self.assertEqual(detect_csv_kind(read_csv(pay)[0]), "payments")
            self.assertEqual(detect_csv_kind(read_csv(links)[0]), "links")
            with self.assertRaises(ValueError):
                ProfileStore(str(pay))


class TestStub(unittest.TestCase):
    def test_cards_in_backend_format(self):
        with tempfile.TemporaryDirectory() as d:
            r = Orchestrator(_settings(d)).run("MOCK-002", 30)
            ids = [c["id"] for c in r.insights]
            self.assertEqual(ids[:2], ["MOCK-002-M05-ceo", "MOCK-002-M16-ceo"])
            self.assertIn("MOCK-002-M01-ceo", ids)
            self.assertNotIn("MOCK-002-M09-ceo", ids)                       # шум
            self.assertNotIn("MOCK-002-M22-ceo", ids)                       # реклама вебинара
            for c in r.insights:
                self.assertLessEqual(BACKEND_KEYS, set(c))
                self.assertIn(c["type"], ("risk", "opportunity", "event"))
                self.assertTrue(0 <= c["importance"] <= 100)
                self.assertTrue(c["sources"] and all(UTC_Z.match(s["published_at"]) for s in c["sources"]))
                d6 = c["details"]
                self.assertTrue(all(s["origin"] == "fact" for s in d6["what_happened"]))
                self.assertTrue(all(s["origin"] == "bank" for s in d6["bank_view"]))
                self.assertEqual(c["generated_by"], "rules")
                self.assertTrue(c["used_profile_fields"])
            self.assertTrue(list(Path(d, "runs").glob("*.json")))

    def test_role_changes_feed(self):
        with tempfile.TemporaryDirectory() as d:
            o = Orchestrator(_settings(d))
            ceo = [c["id"].split("-")[2] for c in o.run("MOCK-004", 30, role="ceo").insights]
            acc = [c["id"].split("-")[2] for c in o.run("MOCK-004", 30, role="accountant").insights]
            ceo2 = [c["id"].split("-")[2] for c in o.run("MOCK-002", 30, role="ceo").insights]
            cfo2 = [c["id"].split("-")[2] for c in o.run("MOCK-002", 30, role="cfo").insights]
            self.assertIn("M11", acc)                              # НДС при экспорте — бухгалтеру экспортёра
            self.assertNotIn("M11", ceo)
            self.assertLess(cfo2.index("M01"), ceo2.index("M01"))  # финдиректору ставка выше, чем гендиректору
            with self.assertRaises(ValueError):
                o.run("MOCK-004", 30, role="intern")

    def test_no_okved_shows_macro_with_flag(self):
        with tempfile.TemporaryDirectory() as d:
            p = CompanyProfile.from_row({"SYNTHETIC_U": "X1", "REGION": "Тверская область"})
            r = Orchestrator(_settings(d)).run(p, 30)
            self.assertEqual([c["id"] for c in r.insights], ["X1-M01-ceo", "X1-M02-ceo"])
            self.assertEqual(r.trace[0].status, "fallback")
            self.assertTrue(any("ОКВЭД" in g for g in r.insights[0]["details"]["data_gaps"]))

    def test_unknown_company(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(CompanyNotFound):
                Orchestrator(_settings(d)).run("NOPE", 30)


class TestSelection(unittest.TestCase):
    def test_macro_not_saturated(self):
        """Решение по ставке: у застройщика с кредитом влияние сильнее, чем у IT без кредита (не оба 100)."""
        from agent.roles import get_role
        from agent.scoring import score_news
        from agent.schemas import NewsItem
        from datetime import date
        st = ProfileStore(Settings().companies_csv)
        news = NewsItem.from_dict({"id": "X", "title": "ЦБ повысил ставку до 15%", "text": "ЦБ повысил ставку.",
                                   "published_at": "2026-10-22", "extraction": {"event_type": "monetary_policy",
                                   "factor_moves": [{"factor": "key_rate", "direction": "up", "strength": "high", "evidence": "-"},
                                                    {"factor": "labor_market", "direction": "up", "strength": "medium", "evidence": "-"}]}})
        m = {cid: score_news(st.get(cid), news, 30, date(2026, 10, 22), get_role("ceo")).parts["macro"]
             for cid in ("MOCK-002", "MOCK-003", "MOCK-005")}
        self.assertGreater(m["MOCK-002"], m["MOCK-003"])
        self.assertGreater(m["MOCK-002"], m["MOCK-005"])
        self.assertLess(m["MOCK-002"], 1.0)

    def test_entities_and_targeting(self):
        with tempfile.TemporaryDirectory() as d:
            o = Orchestrator(_settings(d))
            feed = lambda cid, role="ceo": [c["id"].split("-")[2] for c in o.run(cid, 30, role=role).insights]  # noqa: E731
            self.assertIn("M14", feed("MOCK-001"))        # Турция — страна ВЭД аграрной компании
            self.assertIn("M15", feed("MOCK-006"))        # китайские станки — оптовику оборудования из Китая
            self.assertNotIn("M13", feed("MOCK-003"))     # пошлины Китая на прокат — не рознице
            self.assertNotIn("M20", feed("MOCK-003"))     # программа Новосибирской области — не Татарстану
            self.assertNotIn("M18", feed("MOCK-001"))     # дефицит водителей на юге — не аграрию из того же края

    def test_duplicates_suppressed(self):
        with tempfile.TemporaryDirectory() as d:
            r = Orchestrator(_settings(d)).run("MOCK-006", 30, role="cfo")
            ids = [c["id"].split("-")[2] for c in r.insights]
            self.assertIn("M01", ids)
            self.assertNotIn("M21", ids)                  # перепечатка решения ЦБ
            st = [t for t in r.trace if t.tool == "score_news"][0].output
            self.assertEqual(next(x for x in st if x["news_id"] == "M21")["duplicate_of"], "M01")

    def test_quality_on_labeled_set(self):
        from agent.evaluate import evaluate
        with tempfile.TemporaryDirectory() as d:
            s = evaluate(settings=_settings(d))["summary"]
        # планка, ниже которой правки весов не принимаем (на 23.10: P 0.98, R 1.00, nDCG 0.87)
        self.assertGreaterEqual(s["P@k"], 0.9)
        self.assertGreaterEqual(s["R@k"], 0.9)
        self.assertEqual(s["dup_pairs"], 0)


class TestLiveReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_extract_interpret_selfcheck_then_replay(self):
        with tempfile.TemporaryDirectory() as d:
            FakeModel.calls.clear()
            live = _settings(d, llm_mode="live", llm_base_url=self.url, llm_api_key="test")
            r = Orchestrator(live, news_provider=lambda cid, period: RAW_NEWS).run("MOCK-002", 30, role="cfo")
            kinds = ["extract" if "размечает" in c["messages"][0]["content"] else "interpret" for c in FakeModel.calls]
            self.assertEqual(kinds, ["extract", "interpret", "interpret"])   # разметка, ответ, исправление
            c = r.insights[0]
            self.assertEqual((c["id"], c["generated_by"], c["role"]), ("MOCK-002-B1-cfo", "llm", "cfo"))
            self.assertIn("14%", c["summary"])
            self.assertNotIn("25", c["summary"])
            d6 = c["details"]
            # позиция банка — дословный тезис, на который сослалась модель, а не её пересказ
            theses = {t.sid: t.thesis for t in load_bank_theses()}
            self.assertEqual(len(d6["bank_view"]), 1)
            bv = d6["bank_view"][0]
            self.assertEqual(bv["text"], theses[bv["source_ids"][0]])
            self.assertNotIn("F:ВЫДУМКА", c["used_profile_fields"])
            notes = d6["agent"]["validation_notes"]
            self.assertTrue(any("исправлено после самопроверки" in n for n in notes), notes)
            self.assertTrue(any("дословным тезисом" in n for n in notes), notes)
            self.assertEqual({s["source_kind"] for s in c["sources"]}, {"external", "bank_analytics"})
            self.assertIn("https://example.org/b1", [s["url"] for s in c["sources"]])

            # replay: без ключа и без единого вызова — та же карточка, включая исправление
            FakeModel.calls.clear()
            replay = _settings(d, llm_mode="replay")
            r2 = Orchestrator(replay, news_provider=lambda cid, period: RAW_NEWS).run("MOCK-002", 30, role="cfo")
            self.assertEqual(FakeModel.calls, [])
            c2 = r2.insights[0]
            strip = lambda x: {k: v for k, v in x.items() if k not in ("created_at", "details")}  # noqa: E731
            self.assertEqual(strip(c2), strip(c))
            self.assertEqual(c2["generated_by"], "llm")

    def test_replay_miss_falls_back_to_rules(self):
        with tempfile.TemporaryDirectory() as d:
            r = Orchestrator(_settings(d, llm_mode="replay")).run("MOCK-002", 30)
            self.assertEqual(r.insights[0]["generated_by"], "rules")
            self.assertEqual([t for t in r.trace if t.tool == "interpret"][0].status, "fallback")

    def test_budget_guard(self):
        with tempfile.TemporaryDirectory() as d:
            FakeModel.calls.clear()
            s = _settings(d, llm_mode="live", llm_base_url=self.url, llm_api_key="t", llm_daily_budget_rub=0.0)
            r = Orchestrator(s).run("MOCK-002", 30)
            self.assertEqual(FakeModel.calls, [])
            self.assertEqual(r.insights[0]["generated_by"], "rules")


if __name__ == "__main__":
    unittest.main()
