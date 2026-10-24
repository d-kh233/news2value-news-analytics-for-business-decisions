from app.schemas.digest import Digest
from app.schemas.job import Job, JobAccepted


def test_digest_day_and_week(client, company_id):
    items = client.get(f"/companies/{company_id}/insights", params={"limit": 100}).json()["items"]
    all_ids = {i["id"] for i in items}
    for period in ("day", "week"):
        r = client.get(f"/companies/{company_id}/digest", params={"period": period})
        assert r.status_code == 200
        digest = Digest.model_validate(r.json())
        assert digest.company_id == company_id and digest.period == period
        assert set(digest.insight_ids) <= all_ids
    week = client.get(f"/companies/{company_id}/digest", params={"period": "week"}).json()
    day = client.get(f"/companies/{company_id}/digest", params={"period": "day"}).json()
    assert len(week["insight_ids"]) >= len(day["insight_ids"])


def test_digest_invalid_period_422(client, company_id):
    for params in ({"period": "month"}, {}):
        r = client.get(f"/companies/{company_id}/digest", params=params)
        assert r.status_code == 422
        assert r.json()["error"] == "validation_error"


def test_refresh_returns_202_and_job(bank_client):
    r = bank_client.post("/companies/U_2/insights/refresh")
    assert r.status_code == 202
    accepted = JobAccepted.model_validate(r.json())
    assert set(r.json()) == {"job_id"}

    job = bank_client.get(f"/jobs/{accepted.job_id}")
    assert job.status_code == 200
    parsed = Job.model_validate(job.json())
    assert parsed.id == accepted.job_id
    assert parsed.status == "pending"  # выполняет worker (tests/test_jobs_worker.py)
    assert set(job.json()) == {"id", "status", "error"}  # схему Job не расширяем


def test_job_not_found(db_client):
    r = db_client.get("/jobs/nope")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_result_of_unknown_job_is_404(db_client):
    assert db_client.get("/jobs/nope/result").status_code == 404
