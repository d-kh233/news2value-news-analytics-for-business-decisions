import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import mock_store


@pytest.fixture(autouse=True)
def _reset_mocks():
    """Перед каждым тестом возвращаем моки в исходное состояние."""
    mock_store.reset()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def company_id(client) -> str:
    return client.get("/companies").json()["items"][0]["id"]


@pytest.fixture
def insight_id(client, company_id) -> str:
    return client.get(f"/companies/{company_id}/insights").json()["items"][0]["id"]
