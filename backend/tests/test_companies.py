from app.schemas.common import Page
from app.schemas.company import Company, CompanyProfile

PROFILE = {
    "company_id": "ignored",
    "industry": "Логистика",
    "regions": ["Москва"],
    "key_clients": ["ООО Ромашка"],
    "key_suppliers": ["АО Поставка"],
    "raw_materials": ["дизельное топливо"],
    "currency_risks": ["USD"],
    "debt_load": "умеренная",
    "strategic_goals": ["рост на 10%"],
    "bank_products": ["овердрафт"],
    "manual_data": {"заметка": "добавлено вручную"},
}

NEW_COMPANY = {"name": "Новая", "industry": "IT", "region": "Казань", "revenue_bln_rub": 1.5}


def test_list_companies(client):
    r = client.get("/companies")
    assert r.status_code == 200
    page = Page[Company].model_validate(r.json())
    assert page.total == 3 and len(page.items) == 3
    assert len({c.industry for c in page.items}) == 3


def test_list_companies_pagination(client):
    body = client.get("/companies", params={"limit": 2, "offset": 1}).json()
    assert body["total"] == 3 and body["limit"] == 2 and body["offset"] == 1
    assert len(body["items"]) == 2


def test_get_company(client, company_id):
    r = client.get(f"/companies/{company_id}")
    assert r.status_code == 200
    assert Company.model_validate(r.json()).id == company_id


def test_create_company(client):
    r = client.post("/companies", json=NEW_COMPANY)
    assert r.status_code == 201
    created = Company.model_validate(r.json())
    assert created.name == "Новая"
    assert client.get(f"/companies/{created.id}").status_code == 200
    assert client.get("/companies").json()["total"] == 4


def test_create_company_invalid(client):
    r = client.post("/companies", json={"name": "Без полей"})
    assert r.status_code == 422


def test_profile_get_put(client, company_id):
    r = client.get(f"/companies/{company_id}/profile")
    assert r.status_code == 200
    assert CompanyProfile.model_validate(r.json()).company_id == company_id

    r = client.put(f"/companies/{company_id}/profile", json=PROFILE)
    assert r.status_code == 200
    updated = CompanyProfile.model_validate(r.json())
    assert updated.company_id == company_id  # id берётся из URL
    assert updated.regions == ["Москва"]
    assert client.get(f"/companies/{company_id}/profile").json()["debt_load"] == "умеренная"
    assert updated.manual_data == {"заметка": "добавлено вручную"}


def test_profile_before_put_is_200_with_empty_manual_data(client):
    created = client.post("/companies", json=NEW_COMPANY).json()
    r = client.get(f"/companies/{created['id']}/profile")
    assert r.status_code == 200
    profile = CompanyProfile.model_validate(r.json())
    assert profile.company_id == created["id"]
    # поля, известные из данных компании, заполнены; ручные — пусты
    assert profile.industry == NEW_COMPANY["industry"]
    assert profile.regions == [NEW_COMPANY["region"]]
    assert profile.manual_data == {}
    assert profile.key_clients == [] and profile.bank_products == []
    assert client.put(f"/companies/{created['id']}/profile", json=PROFILE).status_code == 200
    assert client.get(f"/companies/{created['id']}/profile").json()["key_clients"] == [
        "ООО Ромашка"
    ]


def test_profile_of_every_seeded_company_is_200(client):
    for company in client.get("/companies").json()["items"]:
        r = client.get(f"/companies/{company['id']}/profile")
        assert r.status_code == 200
        assert "manual_data" in r.json()


def test_delete_company(client, company_id):
    r = client.delete(f"/companies/{company_id}")
    assert r.status_code == 204
    assert r.content == b""
    assert client.get(f"/companies/{company_id}").status_code == 404
    assert client.get(f"/companies/{company_id}/insights").status_code == 404


def test_unknown_company_is_404(client):
    assert client.get("/companies/nope").status_code == 404
    assert client.get("/companies/nope/profile").status_code == 404
    assert client.put("/companies/nope/profile", json=PROFILE).status_code == 404
    assert client.delete("/companies/nope").status_code == 404
    assert client.get("/companies/nope/insights").status_code == 404
    assert client.get("/companies/nope/digest", params={"period": "day"}).status_code == 404
    assert client.post("/companies/nope/insights/refresh").status_code == 404
