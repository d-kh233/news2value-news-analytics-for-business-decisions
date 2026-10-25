"""Компании на БД (нужна Postgres): список, поиск, создание, удаление, revenue_known."""

from pathlib import Path

from app.schemas.common import Page
from app.schemas.company import Company
from app.services.bank_loader import load_all
from tests.conftest import FIXTURES

NEW_COMPANY = {"name": "Новая", "industry": "IT", "region": "Казань", "revenue_bln_rub": 1.5}
HEADER = (FIXTURES / "companies.csv").read_text(encoding="utf-8").splitlines()[0]


def _ids(body: dict) -> list[str]:
    return [c["id"] for c in body["items"]]


# --- список и поиск ---


def test_list_companies_from_db(bank_client):
    r = bank_client.get("/companies")
    assert r.status_code == 200
    page = Page[Company].model_validate(r.json())
    assert page.total == 9 and len(page.items) == 9
    u1 = next(c for c in page.items if c.id == "U_1")
    # у банковской компании название = id, значения — как в файле
    assert (u1.name, u1.industry, u1.region) == (
        "U_1",
        "Торговля розничная в неспециализированных магазинах",
        "Томская область",
    )
    assert (u1.revenue_bln_rub, u1.revenue_known, u1.source) == (150000.0, True, "bank")


def test_list_companies_pagination(bank_client):
    body = bank_client.get("/companies", params={"limit": 2, "offset": 1}).json()
    assert body["total"] == 9 and body["limit"] == 2 and body["offset"] == 1
    assert _ids(body) == ["U_2", "U_3"]


def test_search_by_industry_case_insensitive(bank_client):
    body = bank_client.get("/companies", params={"q": "ТОРГОВЛЯ ОПТОВАЯ"}).json()
    assert _ids(body) == ["U_2", "U_7"] and body["total"] == 2


def test_search_by_name_and_id(bank_client):
    created = bank_client.post("/companies", json={**NEW_COMPANY, "name": "Ромашка-Сервис"}).json()
    assert _ids(bank_client.get("/companies", params={"q": "ромашка"}).json()) == [created["id"]]
    assert _ids(bank_client.get("/companies", params={"q": "U_9"}).json()) == ["U_9"]


def test_search_like_symbols_are_literal(bank_client):
    # «_» и «%» — обычные символы, а не шаблон: «U%» ничего не находит
    assert bank_client.get("/companies", params={"q": "U%"}).json()["total"] == 0
    assert bank_client.get("/companies", params={"q": "_"}).json()["total"] == 9


def test_search_nothing_found(bank_client):
    body = bank_client.get("/companies", params={"q": "нет такой"}).json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_get_company(bank_client):
    r = bank_client.get("/companies/U_2")
    assert r.status_code == 200
    assert Company.model_validate(r.json()).id == "U_2"


# --- создание ---


def test_create_company(db_client):
    r = db_client.post("/companies", json=NEW_COMPANY)
    assert r.status_code == 201
    created = Company.model_validate(r.json())
    assert (created.name, created.industry, created.region) == ("Новая", "IT", "Казань")
    assert (created.revenue_bln_rub, created.revenue_known, created.source) == (1.5, True, "manual")
    assert created.id.startswith("c-")
    assert db_client.get(f"/companies/{created.id}").json() == r.json()
    assert db_client.get("/companies").json()["total"] == 1


def test_create_company_invalid(db_client):
    assert db_client.post("/companies", json={"name": "Без полей"}).status_code == 422


# --- удаление ---


def test_delete_manual_company_204(bank_client):
    created = bank_client.post("/companies", json=NEW_COMPANY).json()
    r = bank_client.delete(f"/companies/{created['id']}")
    assert r.status_code == 204
    assert r.content == b""
    assert bank_client.get(f"/companies/{created['id']}").status_code == 404
    assert bank_client.get(f"/companies/{created['id']}/profile").status_code == 404


def test_delete_bank_company_409(bank_client):
    r = bank_client.delete("/companies/U_1")
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "conflict"
    assert "данных банка" in body["detail"]
    assert bank_client.get("/companies/U_1").status_code == 200


def test_unknown_company_is_404(db_client):
    client = db_client
    assert client.get("/companies/nope").status_code == 404
    assert client.get("/companies/nope/profile").status_code == 404
    assert client.put("/companies/nope/profile", json={"debt_load": "x"}).status_code == 404
    assert client.delete("/companies/nope").status_code == 404
    assert client.get("/companies/nope/insights").status_code == 404
    assert client.get("/companies/nope/digest", params={"period": "day"}).status_code == 404
    assert client.post("/companies/nope/insights/refresh").status_code == 404


# --- revenue_known ---


def test_revenue_known_from_csv(bank_client):
    u1 = bank_client.get("/companies/U_1").json()
    u4 = bank_client.get("/companies/U_4").json()  # в CSV «Не определено»
    assert (u1["revenue_bln_rub"], u1["revenue_known"]) == (150000.0, True)
    assert (u4["revenue_bln_rub"], u4["revenue_known"]) == (0.0, False)


def test_revenue_known_false_for_empty_revenue(db, db_client, tmp_path: Path):
    companies = tmp_path / "companies.csv"
    empty = ";" * 23  # 24 колонки, все пустые
    companies.write_text(
        f"{HEADER}\nU_EMPTY{empty}\nU_ZERO{';' * 20}0.0{';' * 3}\n", encoding="utf-8"
    )
    load_all(db, companies, FIXTURES / "links.csv", FIXTURES / "payments.csv")

    empty_rev = db_client.get("/companies/U_EMPTY").json()
    zero_rev = db_client.get("/companies/U_ZERO").json()
    assert (empty_rev["revenue_bln_rub"], empty_rev["revenue_known"]) == (0.0, False)
    # настоящий ноль в файле — выручка известна
    assert (zero_rev["revenue_bln_rub"], zero_rev["revenue_known"]) == (0.0, True)


def test_revenue_known_true_for_every_created_company(db_client):
    for revenue in (0.0, 2.5):
        created = db_client.post("/companies", json={**NEW_COMPANY, "revenue_bln_rub": revenue})
        assert created.json()["revenue_known"] is True
    assert all(c["revenue_known"] for c in db_client.get("/companies").json()["items"])
