import logging
import re

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.common import ErrorResponse
from app.services import company_service

UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _collect(node, key_names, found):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in key_names and isinstance(v, str):
                found.append(v)
            _collect(v, key_names, found)
    elif isinstance(node, list):
        for v in node:
            _collect(v, key_names, found)


def test_datetimes_are_utc_with_z(client, company_id, insight_id):
    """Формат времени: 2026-10-21T14:30:00Z, без +00:00 и без долей секунды."""
    bodies = [
        client.get(f"/companies/{company_id}/insights", params={"limit": 100}).json(),
        client.get(f"/insights/{insight_id}").json(),
        client.post(f"/insights/{insight_id}/feedback", json={"rating": "useful"}).json(),
    ]
    found: list[str] = []
    for body in bodies:
        _collect(body, {"created_at", "published_at"}, found)
    assert len(found) > 20
    for value in found:
        assert UTC_Z.match(value), value


def test_error_shape_is_uniform(db_client):
    client = db_client
    for response in (
        client.get("/companies/nope"),
        client.get("/jobs/nope"),
        client.get("/companies", params={"limit": 0}),
        client.get("/no-such-route"),
    ):
        assert not response.is_success
        body = response.json()
        assert set(body) == {"error", "detail"}
        ErrorResponse.model_validate(body)
    assert client.get("/companies", params={"limit": 0}).json()["error"] == "validation_error"
    assert client.get("/companies/nope").json()["error"] == "not_found"


def test_unexpected_exception_gives_500_internal_error(monkeypatch, caplog):
    """Неожиданное исключение: 500 в общей форме, трейсбек только в логе."""

    def boom(*args, **kwargs):
        raise RuntimeError("секретная-деталь-сбоя")

    monkeypatch.setattr(company_service, "list_companies", boom)
    safe_client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        r = safe_client.get("/companies")
    assert r.status_code == 500
    body = r.json()
    assert set(body) == {"error", "detail"}
    assert body["error"] == "internal_error"
    ErrorResponse.model_validate(body)
    assert "секретная-деталь-сбоя" not in r.text
    assert "Traceback" not in r.text
    assert "секретная-деталь-сбоя" in caplog.text  # причина записана в лог
    assert "Traceback" in caplog.text


def test_cors_allows_configured_origin(client):
    r = client.options(
        "/companies",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_blocks_unknown_origin(client):
    r = client.options(
        "/companies",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers


def test_openapi_lists_all_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/companies": {"get", "post"},
        "/companies/{company_id}": {"get", "delete"},
        "/companies/{company_id}/profile": {"get", "put"},
        "/companies/{company_id}/insights": {"get"},
        "/companies/{company_id}/digest": {"get"},
        "/companies/{company_id}/insights/refresh": {"post"},
        "/insights/{insight_id}": {"get"},
        "/insights/{insight_id}/ask": {"post"},
        "/insights/{insight_id}/feedback": {"post"},
        "/jobs/{job_id}": {"get"},
        "/jobs/{job_id}/result": {"get"},
        "/jobs/{job_id}/run": {"get"},
    }
    for path, methods in expected.items():
        assert methods <= set(paths[path]), path
