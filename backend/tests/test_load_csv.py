"""Загрузка CSV банка в БД (нужна Postgres, база news2value_test).

Фикстуры в tests/fixtures/ — маленькие синтетические файлы с теми же колонками,
что у настоящих выгрузок. Номер строки в rejected_rows — номер строки в файле
(заголовок — строка 1).
"""

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Company,
    CompanyProfileRow,
    Payment,
    Person,
    PersonCompanyLink,
    RejectedRow,
)
from app.services.bank_loader import LoadReport, format_report, load_all

FIXTURES = Path(__file__).parent / "fixtures"
COMPANIES = FIXTURES / "companies.csv"
LINKS = FIXTURES / "links.csv"
PAYMENTS = FIXTURES / "payments.csv"


def _load(engine: Engine, payments: Path = PAYMENTS) -> LoadReport:
    # маленькая порция, чтобы проверить загрузку в несколько заходов
    return load_all(engine, COMPANIES, LINKS, payments, chunk_size=4)


def _counts(engine: Engine) -> dict[str, int]:
    with Session(engine) as s:
        return {
            m.__tablename__: s.scalar(select(func.count()).select_from(m))
            for m in (Company, Person, PersonCompanyLink, Payment, RejectedRow)
        }


def _rejected(engine: Engine, source_file: str, reason: str) -> list[RejectedRow]:
    with Session(engine) as s:
        stmt = (
            select(RejectedRow)
            .where(RejectedRow.source_file == source_file, RejectedRow.reason == reason)
            .order_by(RejectedRow.row_number)
        )
        return list(s.scalars(stmt))


def _get(engine: Engine, model, key):
    with Session(engine) as s:
        return s.get(model, key)


@pytest.fixture
def report(db: Engine) -> LoadReport:
    return _load(db)


# --- миграция ---


def test_migration_creates_tables(db_engine: Engine) -> None:
    tables = set(inspect(db_engine).get_table_names())

    assert {
        "companies",
        "persons",
        "person_company_links",
        "payments",
        "rejected_rows",
        "company_profiles",
        "alembic_version",
    } <= tables


def test_models_match_migration(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)

    assert diff == []


# --- отклонённые строки ---


def test_ambiguous_company_id_rejected(report: LoadReport, db: Engine) -> None:
    rows = _rejected(db, "companies.csv", "ambiguous_id")

    assert _get(db, Company, "U_AMB") is None
    assert [r.row_number for r in rows] == [8, 9, 10]
    assert rows[0].raw_data["SYNTHETIC_U"] == "U_AMB"
    assert rows[0].raw_data["REVENUE_COMPANY"] == "1100.0"
    assert report.companies.rejected == {"ambiguous_id": 3, "exact_duplicate": 1}


def test_exact_duplicate_company_kept_once(report: LoadReport, db: Engine) -> None:
    rows = _rejected(db, "companies.csv", "exact_duplicate")

    assert _get(db, Company, "U_7").revenue_company == 770000
    assert [r.row_number for r in rows] == [12]


def test_exact_duplicate_link_detected_after_trim(report: LoadReport, db: Engine) -> None:
    # строки 8 и 9 различаются только пробелами вокруг города
    rows = _rejected(db, "links.csv", "exact_duplicate")

    assert [r.row_number for r in rows] == [9]
    assert _get(db, PersonCompanyLink, ("F_6", "U_6")).city_name == "Казань"


def test_link_same_key_different_data_is_ambiguous(report: LoadReport, db: Engine) -> None:
    rows = _rejected(db, "links.csv", "ambiguous_id")

    assert _get(db, PersonCompanyLink, ("F_7", "U_7")) is None
    assert [r.row_number for r in rows] == [10, 11]


def test_bad_format_row_rejected(db: Engine, tmp_path: Path) -> None:
    bad = tmp_path / "payments_bad.csv"
    bad.write_text(
        "BENEFICIARY;PAYER;PAY_AMT;PAY_CNT\nU_1;U_2;100.0;1\nU_1;U_3;100.0\n", encoding="utf-8"
    )

    rep = _load(db, payments=bad)

    rows = _rejected(db, "payments_bad.csv", "bad_format")
    assert [r.row_number for r in rows] == [3]
    assert rows[0].raw_data == {"values": ["U_1", "U_3", "100.0"]}
    assert rep.payments.rejected == {"bad_format": 1}
    assert rep.payments.loaded == 1


def test_wrong_header_fails_without_partial_load(db: Engine, tmp_path: Path) -> None:
    bad = tmp_path / "payments.csv"
    bad.write_text("PAYER;BENEFICIARY;AMOUNT\nU_1;U_2;1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="колонки"):
        _load(db, payments=bad)

    # вся загрузка — одна транзакция: компании тоже не записались
    assert _counts(db)["companies"] == 0


# --- суммирование платежей ---


def test_payment_sum_after_removing_exact_duplicates(report: LoadReport, db: Engine) -> None:
    # U_2 -> U_1: 1000/1, 1000/1 (полный дубль), 500/2 => 1000 + 500, 1 + 2
    pay = _get(db, Payment, ("U_2", "U_1"))

    assert (pay.pay_amt, pay.pay_cnt, pay.merged_rows) == (1500, 3, 2)
    assert [r.row_number for r in _rejected(db, "payments.csv", "exact_duplicate")] == [3]


def test_payment_sum_for_different_rows(report: LoadReport, db: Engine) -> None:
    pay = _get(db, Payment, ("U_1", "U_3"))

    assert (pay.pay_amt, pay.pay_cnt, pay.merged_rows) == (10000, 6, 2)
    assert report.payments.merged_pairs == [("U_1", "U_3", 2), ("U_2", "U_1", 2)]


def test_payments_of_ambiguous_company_excluded(report: LoadReport, db: Engine) -> None:
    # U_AMB отклонена в компаниях (ambiguous_id): под одним id разные компании,
    # поэтому её платежи (строки 14-16, U_AMB и плательщик, и получатель) не складываем
    with Session(db) as s:
        amb = s.scalar(
            select(func.count())
            .select_from(Payment)
            .where((Payment.payer == "U_AMB") | (Payment.beneficiary == "U_AMB"))
        )
    rows = _rejected(db, "payments.csv", "ambiguous_company")

    assert amb == 0
    assert [r.row_number for r in rows] == [14, 15, 16]
    assert rows[0].raw_data == {
        "BENEFICIARY": "U_1",
        "PAYER": "U_AMB",
        "PAY_AMT": "100.0",
        "PAY_CNT": "1",
    }
    assert all("U_AMB" not in pair for pair in report.payments.merged_pairs)
    assert report.payments.rejected == {"exact_duplicate": 1, "ambiguous_company": 3}


# --- пустые и нестандартные значения ---


def test_spaces_trimmed_and_empty_becomes_null(report: LoadReport, db: Engine) -> None:
    u2 = _get(db, Company, "U_2")
    u1 = _get(db, Company, "U_1")
    link = _get(db, PersonCompanyLink, ("F_2", "U_3"))

    assert u2.eco_mark_label is None  # было "   "
    assert (u2.revenue_company, u2.revenue_company_raw) == (5000000, None)
    assert u1.amount_workers_spark_gradation is None
    assert u1.fts_country_name is None
    assert (link.city_name, link.rc_name) == (None, None)
    assert _get(db, Payment, ("U_6", "U_5")).pay_amt == 2500
    assert _get(db, Payment, ("U_6", "U_5")).pay_cnt == 4


def test_empty_number_is_null_without_raw(report: LoadReport, db: Engine) -> None:
    pay = _get(db, Payment, ("U_5", "U_6"))

    assert (pay.pay_amt, pay.pay_amt_raw, pay.pay_cnt) == (None, None, 1)


def test_not_defined_in_number_goes_to_raw(report: LoadReport, db: Engine) -> None:
    u4 = _get(db, Company, "U_4")
    pay_amt = _get(db, Payment, ("U_3", "U_2"))
    pay_cnt = _get(db, Payment, ("U_8", "U_7"))

    assert (u4.revenue_company, u4.revenue_company_raw) == (None, "Не определено")
    assert (pay_amt.pay_amt, pay_amt.pay_amt_raw) == (None, "Не определено")
    assert (pay_amt.pay_cnt, pay_amt.pay_cnt_raw) == (2, None)
    assert (pay_cnt.pay_amt, pay_cnt.pay_cnt, pay_cnt.pay_cnt_raw) == (100, None, "н/д")


def test_not_defined_in_text_kept_as_text(report: LoadReport, db: Engine) -> None:
    assert _get(db, PersonCompanyLink, ("F_3", "U_4")).rc_name == "ИНН не определён"
    assert _get(db, PersonCompanyLink, ("F_8", "U_405")).rc_name == "Не определено"


def test_flags_and_codes(report: LoadReport, db: Engine) -> None:
    u1, u2, u3 = (_get(db, Company, k) for k in ("U_1", "U_2", "U_3"))

    assert (u1.negativemark, u1.cl_product_rko_mark) == (False, False)
    assert (u2.negativemark, u2.cl_product_rko_mark, u2.fts_flag) == (True, True, True)
    # код ОКВЭД — текст как в файле
    assert (u2.mainokved_code, u3.mainokved_code) == ("46.34.2", "69")


def test_invalid_flag_is_null_and_counted(report: LoadReport, db: Engine) -> None:
    assert _get(db, Company, "U_5").negativemark is None
    assert report.companies.invalid_values == {"negativemark": 1, "revenue_company": 1}


def test_revenue_1000_counted(report: LoadReport, db: Engine) -> None:
    assert _get(db, Company, "U_3").revenue_company == 1000
    assert report.companies.revenue_eq_1000 == 1


# --- неизвестные компании ---


def test_unknown_companies_loaded_and_flagged(report: LoadReport, db: Engine) -> None:
    assert _get(db, PersonCompanyLink, ("F_1", "U_1")).is_known_company is True
    assert _get(db, PersonCompanyLink, ("F_4", "U_404")).is_known_company is False
    # компания с неоднозначным id не загружена, значит для связи она неизвестна
    assert _get(db, PersonCompanyLink, ("F_5", "U_AMB")).is_known_company is False

    unknown_payer = _get(db, Payment, ("U_999", "U_1"))
    unknown_benef = _get(db, Payment, ("U_1", "U_404"))
    assert (unknown_payer.payer_is_known, unknown_payer.beneficiary_is_known) == (False, True)
    assert (unknown_benef.payer_is_known, unknown_benef.beneficiary_is_known) == (True, False)

    assert report.links.unknown == {"synthetic_u": 3}
    assert report.payments.unknown == {"payer": 1, "beneficiary": 1, "any": 2}


def test_persons_from_loaded_links(report: LoadReport, db: Engine) -> None:
    with Session(db) as s:
        persons = set(s.scalars(select(Person.synthetic_f)))

    # F_7 есть только в отклонённых строках
    assert persons == {"F_1", "F_2", "F_3", "F_4", "F_5", "F_6", "F_8"}
    assert report.persons_total == 7


# --- отчёт ---


def test_report_balances(report: LoadReport, db: Engine) -> None:
    c, ln, p = report.companies, report.links, report.payments

    assert (c.read, c.loaded, sum(c.rejected.values()), c.merged) == (13, 9, 4, 0)
    assert (ln.read, ln.loaded, sum(ln.rejected.values()), ln.merged) == (12, 9, 3, 0)
    assert (p.read, p.loaded, sum(p.rejected.values()), p.merged) == (15, 9, 4, 2)
    for f in (c, ln, p):
        assert f.balanced
        assert f.table_rows == f.loaded
    assert _counts(db) == {
        "companies": 9,
        "persons": 7,
        "person_company_links": 9,
        "payments": 9,
        "rejected_rows": 11,
    }


def test_report_null_counts(report: LoadReport) -> None:
    assert report.companies.null_counts["eco_mark_label"] == 1
    assert report.companies.null_counts["amount_workers_spark_gradation"] == 7
    assert report.companies.null_counts["revenue_company"] == 1
    assert report.payments.null_counts["pay_amt"] == 2
    assert report.links.null_counts["city_name"] == 1


def test_report_text(report: LoadReport) -> None:
    out = format_report(report)

    assert "U_2 -> U_1" in out
    assert "Вопрос к лиду" in out
    assert "ambiguous_id" in out


# --- повторный запуск ---


def test_rerun_changes_nothing(db: Engine) -> None:
    first = _load(db)
    counts = _counts(db)
    with Session(db) as s:
        sums = s.execute(
            select(
                func.sum(Payment.pay_amt), func.sum(Payment.pay_cnt), func.sum(Payment.merged_rows)
            )
        ).one()

    second = _load(db)

    assert _counts(db) == counts
    with Session(db) as s:
        assert (
            s.execute(
                select(
                    func.sum(Payment.pay_amt),
                    func.sum(Payment.pay_cnt),
                    func.sum(Payment.merged_rows),
                )
            ).one()
            == sums
        )
    assert _get(db, Payment, ("U_2", "U_1")).pay_amt == 1500
    assert (first.payments.new, second.payments.new) == (9, 0)
    assert (first.companies.new, second.companies.new) == (9, 0)
    assert second.payments.rejected == first.payments.rejected
    assert second.payments.merged_pairs == first.payments.merged_pairs


def test_reload_removes_rows_missing_from_file(db: Engine, tmp_path: Path) -> None:
    # таблицы = последний загруженный файл: строки, которых в файле больше нет, удаляются
    _load(db)
    links = tmp_path / "links.csv"
    links.write_text("SYNTHETIC_F;CITY_NAME;RC_NAME;SYNTHETIC_U\nF_1;Москва;;U_1\n", "utf-8")
    payments = tmp_path / "payments.csv"
    payments.write_text("BENEFICIARY;PAYER;PAY_AMT;PAY_CNT\nU_1;U_2;1000.0;1\n", "utf-8")

    rep = load_all(db, COMPANIES, links, payments)

    counts = _counts(db)
    assert (counts["payments"], counts["person_company_links"], counts["persons"]) == (1, 1, 1)
    assert (rep.payments.removed, rep.links.removed, rep.companies.removed) == (8, 8, 0)
    assert _get(db, Payment, ("U_2", "U_1")).pay_amt == 1000
    # отклонённые строки старого payments.csv не остались висеть
    assert _rejected(db, "payments.csv", "exact_duplicate") == []
    assert _rejected(db, "payments.csv", "ambiguous_company") == []


# --- source и revenue_known (B3) ---


def _add_manual_company(engine: Engine, company_id: str = "c-manual") -> None:
    with Session(engine) as s, s.begin():
        s.add(Company(synthetic_u=company_id, mainokved_name="IT", source="manual"))
        s.add(CompanyProfileRow(company_id=company_id, manual_data={"name": "Ручная"}))


def test_loaded_companies_are_bank_with_revenue_known(report: LoadReport, db: Engine) -> None:
    u1, u4 = _get(db, Company, "U_1"), _get(db, Company, "U_4")

    assert (u1.source, u1.revenue_known) == ("bank", True)
    # «Не определено» — выручка неизвестна, как и пустая
    assert (u4.source, u4.revenue_known, u4.revenue_company) == ("bank", False, None)


def test_rerun_keeps_manual_companies(db: Engine, tmp_path: Path) -> None:
    _load(db)
    _add_manual_company(db)
    # в новом файле только U_1: банковские U_2…U_9 удаляются, ручная остаётся
    companies = tmp_path / "companies.csv"
    lines = COMPANIES.read_text(encoding="utf-8").splitlines()
    companies.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")

    rep = load_all(db, companies, LINKS, PAYMENTS)

    manual = _get(db, Company, "c-manual")
    assert (manual.source, manual.mainokved_name) == ("manual", "IT")
    assert _get(db, CompanyProfileRow, "c-manual").manual_data == {"name": "Ручная"}
    assert rep.companies.removed == 8
    assert rep.companies.manual_rows == 1
    with Session(db) as s:
        stmt = select(Company.source, func.count()).group_by(Company.source)
        sources = {source: n for source, n in s.execute(stmt)}
    assert sources == {"bank": 1, "manual": 1}


def test_bank_row_does_not_overwrite_manual_with_same_id(db: Engine) -> None:
    # id совпал случайно: строку source='manual' загрузка не перезаписывает
    _add_manual_company(db, "U_1")

    _load(db)

    u1 = _get(db, Company, "U_1")
    assert (u1.source, u1.mainokved_name, u1.revenue_company) == ("manual", "IT", None)


def test_report_mentions_manual_rows(db: Engine) -> None:
    _add_manual_company(db)

    out = format_report(_load(db))

    assert "из них source='manual': 1" in out
    assert "синхронизацией не затронуты" in out
