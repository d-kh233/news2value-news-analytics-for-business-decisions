"""Профиль компании: auto из таблиц банка + manual, field_sources (нужна Postgres).

Фикстуры (tests/fixtures/payments.csv) для U_1:
  покупатели (платят U_1): U_999 — 4000 (нет в companies), U_2 — 1500 (U_AMB исключена);
  поставщики (U_1 платит): U_3 — 10000, U_404 — 2000 (нет в companies).
"""

from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models import CompanyProfileRow
from app.schemas.company import PROFILE_FIELDS, CompanyProfile
from app.services.bank_loader import load_all
from tests.conftest import FIXTURES

HEADER = (FIXTURES / "companies.csv").read_text(encoding="utf-8").splitlines()[0]


def _profile(client, company_id: str) -> CompanyProfile:
    r = client.get(f"/companies/{company_id}/profile")
    assert r.status_code == 200, r.text
    return CompanyProfile.model_validate(r.json())


# --- автоматическая часть ---


def test_profile_full_data(bank_client):
    p = _profile(bank_client, "U_2")

    assert p.company_id == "U_2" and p.name == "U_2"
    assert (p.industry, p.okved_code) == ("Торговля оптовая напитками", "46.34.2")
    assert (p.regions, p.city) == (["Москва"], "г. Москва")
    assert (p.revenue_bln_rub, p.revenue_known, p.employees) == (5000000.0, True, "16-100")
    assert p.bank_products == ["РКО", "Кредиты", "Зарплатный проект"]
    assert (p.foreign_trade, p.foreign_trade_countries) == (True, ["КИТАЙ", "ТУРЦИЯ"])
    assert (p.group_id, p.group_size) == ("group_1_sb", 2)
    # U_3 -> U_2: сумма «Не определено» — контрагент есть, сумма неизвестна
    assert [(c.company_id, c.amount) for c in p.top_buyers] == [("U_3", None)]
    assert [(c.company_id, c.amount, c.industry) for c in p.top_suppliers] == [
        ("U_1", 1500, "Торговля розничная в неспециализированных магазинах")
    ]
    assert set(p.field_sources) == set(PROFILE_FIELDS)
    assert set(p.field_sources.values()) == {"auto"}


def test_top_counterparties_sorted_with_industries(bank_client):
    p = _profile(bank_client, "U_1")

    assert [(c.company_id, c.amount, c.is_known) for c in p.top_buyers] == [
        ("U_999", 4000, False),
        ("U_2", 1500, True),
    ]
    assert [(c.company_id, c.industry) for c in p.top_suppliers] == [
        ("U_3", "Деятельность в области права"),
        ("U_404", ""),  # внешний контрагент: отрасль неизвестна
    ]
    assert p.key_clients == ["U_999", "U_2"] and p.key_suppliers == ["U_3", "U_404"]


def test_top_counterparties_limited_to_5(db: Engine, db_client, tmp_path: Path):
    payments = tmp_path / "payments.csv"
    rows = "".join(f"U_1;P_{i};{i * 100}.0;1\n" for i in range(1, 8))
    payments.write_text(f"BENEFICIARY;PAYER;PAY_AMT;PAY_CNT\n{rows}", encoding="utf-8")
    load_all(db, FIXTURES / "companies.csv", FIXTURES / "links.csv", payments)

    buyers = _profile(db_client, "U_1").top_buyers
    assert [c.company_id for c in buyers] == ["P_7", "P_6", "P_5", "P_4", "P_3"]


def test_profile_without_payments(bank_client):
    p = _profile(bank_client, "U_9")

    assert p.top_buyers == [] and p.top_suppliers == []
    assert p.key_clients == [] and p.key_suppliers == []
    assert p.group_size == 2  # U_8 и U_9


def test_self_payment_not_counterparty(bank_client):
    # U_4 -> U_4 в файле есть, но сама себе компания не контрагент
    p = _profile(bank_client, "U_4")
    assert p.top_buyers == [] and p.top_suppliers == []
    assert (p.revenue_bln_rub, p.revenue_known) == (0.0, False)  # «Не определено»


def test_profile_with_empty_fields(db: Engine, db_client, tmp_path: Path):
    companies = tmp_path / "companies.csv"
    companies.write_text(f"{HEADER}\nU_EMPTY{';' * 23}\n", encoding="utf-8")
    load_all(db, companies, FIXTURES / "links.csv", FIXTURES / "payments.csv")

    p = _profile(db_client, "U_EMPTY")

    assert (p.name, p.industry, p.okved_code, p.city, p.employees) == ("U_EMPTY", "", "", "", "")
    assert p.regions == [] and p.bank_products == [] and p.foreign_trade_countries == []
    assert (p.foreign_trade, p.group_id, p.group_size) == (None, "", 0)
    assert (p.revenue_bln_rub, p.revenue_known) == (0.0, False)


def test_auto_data_snapshot_saved(bank_client, db: Engine):
    _profile(bank_client, "U_2")
    with Session(db) as s:
        row = s.get(CompanyProfileRow, "U_2")
    assert row.auto_data["okved_code"] == "46.34.2"
    assert row.manual_data == {}


# --- ручная часть ---


def test_manual_wins_over_auto(bank_client):
    r = bank_client.put(
        "/companies/U_1/profile",
        json={"industry": "Сеть магазинов у дома", "raw_materials": ["молоко"], "name": "АО Демо"},
    )
    assert r.status_code == 200
    p = CompanyProfile.model_validate(r.json())

    assert (p.industry, p.raw_materials, p.name) == ("Сеть магазинов у дома", ["молоко"], "АО Демо")
    assert p.field_sources["industry"] == "manual"
    assert p.field_sources["raw_materials"] == "manual"
    # не переданные поля — из данных банка
    assert (p.okved_code, p.field_sources["okved_code"]) == ("47.11", "auto")
    assert _profile(bank_client, "U_1") == p
    # ручное название видно и в карточке компании
    assert bank_client.get("/companies/U_1").json()["name"] == "АО Демо"


def test_explicit_empty_value_wins(bank_client):
    assert _profile(bank_client, "U_1").key_clients == ["U_999", "U_2"]

    p = CompanyProfile.model_validate(
        bank_client.put("/companies/U_1/profile", json={"key_clients": []}).json()
    )

    assert p.key_clients == [] and p.field_sources["key_clients"] == "manual"
    assert p.key_suppliers == ["U_3", "U_404"] and p.field_sources["key_suppliers"] == "auto"


def test_put_keeps_earlier_manual_fields(bank_client):
    bank_client.put("/companies/U_1/profile", json={"debt_load": "умеренная"})
    p = CompanyProfile.model_validate(
        bank_client.put("/companies/U_1/profile", json={"strategic_goals": ["рост"]}).json()
    )
    assert (p.debt_load, p.strategic_goals) == ("умеренная", ["рост"])
    assert p.field_sources["debt_load"] == p.field_sources["strategic_goals"] == "manual"


def test_put_ignores_company_id_and_field_sources(bank_client):
    profile = _profile(bank_client, "U_1").model_dump(mode="json")
    body = {"company_id": "other", "field_sources": profile["field_sources"], "debt_load": "x"}
    p = bank_client.put("/companies/U_1/profile", json=body).json()
    assert p["company_id"] == "U_1"
    assert p["field_sources"]["debt_load"] == "manual"
    assert p["field_sources"]["industry"] == "auto"


def test_free_form_manual_data_kept(bank_client):
    p = bank_client.put(
        "/companies/U_1/profile", json={"manual_data": {"заметка": "добавлено вручную"}}
    ).json()
    assert p["manual_data"] == {"заметка": "добавлено вручную"}
    assert p["field_sources"]["manual_data"] == "manual"


def test_put_invalid_json_422(bank_client):
    bad_type = bank_client.put("/companies/U_1/profile", json={"key_clients": "строка"})
    unknown = bank_client.put("/companies/U_1/profile", json={"key_clientz": []})
    broken = bank_client.put(
        "/companies/U_1/profile",
        content=b'{"key_clients": [',
        headers={"Content-Type": "application/json"},
    )
    for r in (bad_type, unknown, broken):
        assert r.status_code == 422
        assert r.json()["error"] == "validation_error"
    # ручная часть не изменилась
    assert set(_profile(bank_client, "U_1").field_sources.values()) == {"auto"}


def test_profile_of_created_company(db_client):
    created = db_client.post(
        "/companies",
        json={"name": "Новая", "industry": "IT", "region": "Казань", "revenue_bln_rub": 1.5},
    ).json()
    p = _profile(db_client, created["id"])

    assert (p.name, p.field_sources["name"]) == ("Новая", "manual")
    assert (p.industry, p.field_sources["industry"]) == ("IT", "auto")
    assert (p.revenue_bln_rub, p.revenue_known) == (1.5, True)
    assert p.regions == ["Казань"] and p.manual_data == {}
    assert p.top_buyers == [] and p.bank_products == []
    # PUT без name: название, введённое при создании, остаётся
    db_client.put(f"/companies/{created['id']}/profile", json={"debt_load": "низкая"})
    assert db_client.get(f"/companies/{created['id']}").json()["name"] == "Новая"
