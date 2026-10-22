"""Репозиторий компаний на данных из фикстур (нужна Postgres, база news2value_test)."""

from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.repositories import company_repo
from app.services.bank_loader import load_all

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def session(db: Engine):
    load_all(db, FIXTURES / "companies.csv", FIXTURES / "links.csv", FIXTURES / "payments.csv")
    with Session(db) as s:
        yield s


def test_get_by_id(session: Session) -> None:
    company = company_repo.get_by_id(session, "U_1")

    assert company is not None
    assert company.group_id == "group_1_sb"
    assert company.revenue_company == 150000


def test_get_by_id_missing(session: Session) -> None:
    assert company_repo.get_by_id(session, "U_404") is None


def test_list_first_page(session: Session) -> None:
    items, total = company_repo.list_companies(session, limit=3, offset=0)

    assert [c.synthetic_u for c in items] == ["U_1", "U_2", "U_3"]
    assert total == 9


def test_list_last_page(session: Session) -> None:
    items, total = company_repo.list_companies(session, limit=3, offset=8)

    assert [c.synthetic_u for c in items] == ["U_9"]
    assert total == 9


def test_list_offset_out_of_range(session: Session) -> None:
    items, total = company_repo.list_companies(session, limit=3, offset=100)

    assert items == []
    assert total == 9
