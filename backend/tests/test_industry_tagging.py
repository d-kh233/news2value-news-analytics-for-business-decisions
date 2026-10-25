"""Разметка документов по отраслям (okved:NN) и контрагенты для агента (feat/llm-fixes-daria)."""

from sqlalchemy.orm import Session

from app.ml_ports import agent_port, tagging_port
from app.repositories import document_repo
from app.services.industry_tagging import format_report, tag_documents
from tests.conftest import add_news


def _tags(db, doc_id: int) -> list[str]:
    with Session(db) as session:
        return list(document_repo.get_many(session, [doc_id])[doc_id].tags)


def test_tag_documents_adds_industry_and_keeps_source_tags(db):
    ipoteka = add_news(
        db, 1, "Застройщики просят продлить льготную ипотеку", "Продажи новостроек падают.", 21
    )
    rate = add_news(db, 2, "ЦБ сохранил ключевую ставку", "Банк России сохранил ставку 14%.", 22)
    report = tag_documents(db)
    assert (report.total, report.tagged, report.changed) == (2, 1, 1)
    assert _tags(db, ipoteka) == ["economy", "okved:41"]
    assert _tags(db, rate) == ["economy"]
    assert "okved:41: 1" in format_report(report)
    # повторный запуск ничего не меняет
    assert tag_documents(db).changed == 0


def test_fingerprint_changes_with_tags(db):
    add_news(
        db, 1, "Застройщики просят продлить льготную ипотеку", "Продажи новостроек падают.", 21
    )
    with Session(db) as session:
        before = document_repo.news_fingerprint(session)
    tag_documents(db)
    with Session(db) as session:
        assert document_repo.news_fingerprint(session) != before


def test_normalize_industry():
    assert tagging_port.normalize_industry("41") == "okved:41"
    assert tagging_port.normalize_industry("1") == "okved:01"
    assert tagging_port.normalize_industry(" OKVED:41 ") == "okved:41"
    assert tagging_port.normalize_industry("economy") == "economy"


def test_counterparties_for_agent(bank_client):
    from app.core.db import get_session

    with get_session() as session:
        cps = agent_port.counterparties(session, "U_2")
    assert cps and all(c["id"].startswith("P:") for c in cps)
    assert {c["direction"] for c in cps} <= {"покупатель", "поставщик"}
    row = agent_port.row_from_db("U_2")
    assert row["counterparties"] == cps
