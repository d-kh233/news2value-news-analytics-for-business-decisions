"""Обновление инсайтов агентом через задачу: POST .../refresh → worker → таблица insights.

Агент в режиме LLM_MODE=stub на моковых новостях (NEWS_SOURCE=mock), компания — из БД.
Лента GET .../insights до B7 читает моки, поэтому карточки проверяются в таблице insights.
"""

import re

from app import worker
from app.ml_ports import agent_port
from app.repositories import insight_repo
from app.schemas.insight import Insight

UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
BLOCKS = {"what_happened", "why_it_matters", "consequences", "actions", "bank_view"}
DRINKS = "U_2"


def _refresh(client, company_id, **params):
    """Поставить задачу, выполнить её worker'ом; вернуть job_id и итоговый статус."""
    r = client.post(f"/companies/{company_id}/insights/refresh", params=params)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert worker.run_once() is True
    return job_id, client.get(f"/jobs/{job_id}").json()


def _saved(company_id: str) -> list[dict]:
    from app.core.db import get_session

    with get_session() as session:
        return [row.payload for row in insight_repo.list_for_company(session, company_id)]


def test_refresh_saves_agent_cards(bank_client, modes):
    modes("real", "mock")
    job_id, job = _refresh(bank_client, DRINKS, role="cfo")
    assert job["status"] == "done", job
    items = _saved(DRINKS)
    assert items
    for item in items:
        insight = Insight.model_validate(item)
        assert insight.role == "cfo" and insight.generated_by in ("llm", "rules")
        assert BLOCKS <= set(insight.details)
        assert insight.details["agent"]["run_id"]
        assert insight.sources and all(UTC_Z.match(s["published_at"]) for s in item["sources"])
        assert UTC_Z.match(item["created_at"])
    # панель трассировки получает план и шаги агента
    run = bank_client.get(f"/jobs/{job_id}/run").json()
    assert run["role"] == "cfo" and len(run["trace"]) > 3 and run["plan"]
    assert run["llm_mode"] == "stub" and "calls" in run["llm_usage"]


def test_refresh_default_role_is_ceo(bank_client, modes):
    modes("real", "mock")
    _, job = _refresh(bank_client, DRINKS)
    assert job["status"] == "done"
    assert {i["role"] for i in _saved(DRINKS)} == {"ceo"}


def test_agent_failure_keeps_old_insights(bank_client, modes, monkeypatch):
    modes("real", "mock")
    _refresh(bank_client, DRINKS)
    before = _saved(DRINKS)

    def boom(*args, **kwargs):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(agent_port, "generate_insights", boom)
    job_id, job = _refresh(bank_client, DRINKS)
    assert job["status"] == "failed" and "модель недоступна" in job["error"]
    assert _saved(DRINKS) == before
    assert bank_client.get(f"/jobs/{job_id}/run").status_code == 404


def test_mock_insights_have_no_agent_fields(client, insight_id):
    """Моки не меняются: новые поля необязательные, фронт может их не читать."""
    body = client.get(f"/insights/{insight_id}").json()
    assert body["details"] is None and body["role"] is None


def test_row_from_profile_reads_manual_data_and_products():
    row = agent_port.row_from_profile(
        {"id": "c-9", "industry": "Логистика", "region": "Москва"},
        {
            "bank_products": ["овердрафт", "лизинг"],
            "currency_risks": ["лизинг техники в евро"],
            "manual_data": {"MAINOKVED_CODE": "49.41"},
        },
    )
    assert row["MAINOKVED_CODE"] == "49.41" and row["SYNTHETIC_U"] == "c-9"
    assert row["CL_PRODUCT_LOA_MARK"] == 1 and row["CL_PRODUCT_LEA_MARK"] == 1
    assert row["CL_PRODUCT_DEP_MARK"] == 0 and row["FTS_FLAG"] == 1
