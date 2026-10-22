"""Обновление инсайтов агентом: POST /companies/{id}/insights/refresh (агент в режиме stub)."""

import re

from app.ml_ports import agent_port
from app.schemas.insight import Insight

UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
BLOCKS = {"what_happened", "why_it_matters", "consequences", "actions", "bank_view"}


def _refresh(client, company_id, **params):
    r = client.post(f"/companies/{company_id}/insights/refresh", params=params)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    return job_id, client.get(f"/jobs/{job_id}").json()


def _insights(client, company_id):
    r = client.get(f"/companies/{company_id}/insights", params={"limit": 100})
    return r.json()["items"]


def test_refresh_replaces_insights_with_agent_cards(client):
    old_ids = {i["id"] for i in _insights(client, "c-001")}
    job_id, job = _refresh(client, "c-001", role="cfo")
    assert job["status"] == "done", job
    items = _insights(client, "c-001")
    assert items and not ({i["id"] for i in items} & old_ids)
    for item in items:
        insight = Insight.model_validate(item)
        assert insight.role == "cfo" and insight.generated_by in ("llm", "rules")
        assert BLOCKS <= set(insight.details)
        assert insight.details["agent"]["run_id"]
        assert insight.sources and all(UTC_Z.match(s["published_at"]) for s in item["sources"])
        assert UTC_Z.match(item["created_at"])
    # карточка открывается по id, панель трассировки получает план и шаги агента
    assert client.get(f"/insights/{items[0]['id']}").status_code == 200
    run = client.get(f"/jobs/{job_id}/run").json()
    assert run["role"] == "cfo" and len(run["trace"]) > 3 and run["plan"]


def test_refresh_default_role_is_ceo(client):
    _, job = _refresh(client, "c-003")
    assert job["status"] == "done"
    assert {i["role"] for i in _insights(client, "c-003")} == {"ceo"}


def test_refresh_unknown_role_422(client):
    r = client.post("/companies/c-001/insights/refresh", params={"role": "intern"})
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_agent_failure_keeps_old_insights(client, monkeypatch):
    before = _insights(client, "c-002")

    def boom(*args, **kwargs):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(agent_port, "generate_insights", boom)
    job_id, job = _refresh(client, "c-002")
    assert job["status"] == "failed" and "модель недоступна" in job["error"]
    assert _insights(client, "c-002") == before
    assert client.get(f"/jobs/{job_id}/run").status_code == 404


def test_mock_insights_have_no_agent_fields(client, insight_id):
    """Моки не меняются: новые поля необязательные, фронт может их не читать."""
    body = client.get(f"/insights/{insight_id}").json()
    assert body["details"] is None and body["role"] is None


def test_row_from_profile_reads_manual_data_and_products():
    row = agent_port.row_from_profile(
        {"id": "c-9", "industry": "Логистика", "region": "Москва"},
        {"bank_products": ["овердрафт", "лизинг"], "currency_risks": ["лизинг техники в евро"],
         "manual_data": {"MAINOKVED_CODE": "49.41"}},
    )
    assert row["MAINOKVED_CODE"] == "49.41" and row["SYNTHETIC_U"] == "c-9"
    assert row["CL_PRODUCT_LOA_MARK"] == 1 and row["CL_PRODUCT_LEA_MARK"] == 1
    assert row["CL_PRODUCT_DEP_MARK"] == 0 and row["FTS_FLAG"] == 1
