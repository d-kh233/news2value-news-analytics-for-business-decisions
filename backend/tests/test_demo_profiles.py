"""Загрузка демо-профилей (tests/fixtures/demo_profiles) в manual_data (нужна Postgres)."""

from sqlalchemy import Engine

from app.services.demo_profiles import format_report, load_dir
from tests.conftest import FIXTURES

DEMO = FIXTURES / "demo_profiles"


def test_demo_profiles_loaded_and_bad_files_skipped(bank_client, db: Engine):
    report = load_dir(db, DEMO)

    assert report.loaded == ["U_2"]
    assert set(report.skipped) == {"U_404.json", "bad_field.json", "broken.json"}
    assert "не найдена" in report.skipped["U_404.json"]
    assert "Пропущено файлов: 3" in format_report(report)


def test_demo_name_and_fields_visible_in_api(bank_client, db: Engine):
    load_dir(db, DEMO)

    company = bank_client.get("/companies/U_2").json()
    profile = bank_client.get("/companies/U_2/profile").json()

    assert company["name"] == "ООО «Напитки Демо»" and company["source"] == "bank"
    assert profile["raw_materials"] == ["стеклотара", "сахар"]
    assert profile["key_clients"] == []  # явно пустое — ручное
    assert profile["field_sources"]["key_clients"] == "manual"
    assert profile["field_sources"]["okved_code"] == "auto"
    found = bank_client.get("/companies", params={"q": "напитки демо"}).json()
    assert [c["id"] for c in found["items"]] == ["U_2"]


def test_demo_rerun_replaces_manual_data(bank_client, db: Engine):
    bank_client.put("/companies/U_2/profile", json={"debt_load": "старое значение"})

    load_dir(db, DEMO)
    load_dir(db, DEMO)

    profile = bank_client.get("/companies/U_2/profile").json()
    # файл — источник правды: ручное поле не из файла убрано
    assert profile["field_sources"]["debt_load"] == "auto"
    assert profile["name"] == "ООО «Напитки Демо»"
