"""Оценки инсайтов на БД (B7b): одна оценка на пользователя, GET своей оценки, статистика."""

import pytest
from sqlalchemy import text

from app.core.auth import User, current_user
from app.main import app
from tests.test_insights_db import add_insight


@pytest.fixture
def cards(db, bank_client):
    add_insight(db, "r1", "risk", 80, 22, [])
    add_insight(db, "r2", "risk", 70, 22, [])
    add_insight(db, "o1", "opportunity", 60, 22, [])
    add_insight(db, "e1", "event", 50, 22, [])
    return bank_client


@pytest.fixture
def as_user():
    def set_user(user: User) -> None:
        app.dependency_overrides[current_user] = lambda: user

    yield set_user
    app.dependency_overrides.pop(current_user, None)


def _rows(db) -> list[tuple]:
    with db.connect() as conn:
        sql = (
            "SELECT insight_id, user_id, rating, reason, comment, insight_type, created_at, "
            "updated_at FROM insight_feedback ORDER BY id"
        )
        return list(conn.execute(text(sql)))


def test_repeat_feedback_updates_single_row(cards, db):
    url = "/insights/r1/feedback"
    first = cards.post(url, json={"rating": "useful", "comment": "Полезно"})
    assert first.status_code == 201
    second = cards.post(url, json={"rating": "not_useful", "reason": "inaccurate"})
    assert second.status_code == 200
    body = second.json()
    assert (body["rating"], body["reason"], body["comment"]) == ("not_useful", "inaccurate", None)
    assert body["created_at"] == first.json()["created_at"]
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0][:6] == ("r1", "demo", "not_useful", "inaccurate", None, "risk")
    assert rows[0][7] >= rows[0][6]


def test_get_my_feedback(cards):
    url = "/insights/o1/feedback"
    r = cards.get(url)
    assert r.status_code == 200 and r.json() is None
    cards.post(url, json={"rating": "useful"})
    assert cards.get(url).json()["rating"] == "useful"
    assert cards.get("/insights/nope/feedback").status_code == 404


def test_feedback_validation(cards):
    url = "/insights/r1/feedback"
    for body in (
        {"rating": "useful", "reason": "unclear"},  # причина — только для not_useful
        {"rating": "not_useful", "reason": "boring"},
        {"rating": "useful", "comment": "я" * 501},
        {"rating": "meh"},
    ):
        r = cards.post(url, json=body)
        assert r.status_code == 422, body
        assert r.json()["error"] == "validation_error"
    assert (
        "reason указывается только при rating=not_useful"
        in cards.post(url, json={"rating": "useful", "reason": "unclear"}).json()["detail"]
    )
    assert cards.post("/insights/nope/feedback", json={"rating": "useful"}).status_code == 404


def test_one_rating_per_user(cards, db, as_user):
    as_user(User(id="u1"))
    assert cards.post("/insights/r1/feedback", json={"rating": "useful"}).status_code == 201
    as_user(User(id="u2"))
    assert cards.post("/insights/r1/feedback", json={"rating": "not_useful"}).status_code == 201
    assert cards.get("/insights/r1/feedback").json()["rating"] == "not_useful"
    assert len(_rows(db)) == 2


def test_foreign_insight_403(cards, as_user):
    as_user(User(id="u1", company_ids=frozenset({"U_1"})))
    assert cards.post("/insights/r1/feedback", json={"rating": "useful"}).status_code == 403
    assert cards.get("/insights/r1/feedback").status_code == 403


def test_stats_exact(cards, db, as_user):
    votes = {
        "u1": [("r1", "useful", None), ("o1", "useful", None), ("e1", "not_useful", "unclear")],
        "u2": [("r1", "not_useful", "inaccurate"), ("r2", "not_useful", "irrelevant"),
               ("o1", "useful", None)],
        "u3": [("r2", "not_useful", None), ("e1", "useful", None)],
    }  # fmt: skip
    for user, items in votes.items():
        as_user(User(id=user))
        for insight, rating, reason in items:
            body = {"rating": rating, **({"reason": reason} if reason else {})}
            assert cards.post(f"/insights/{insight}/feedback", json=body).status_code == 201
    as_user(User(id="u1"))  # переоценка не добавляет строку
    cards.post("/insights/e1/feedback", json={"rating": "useful"})
    as_user(User(id="admin", is_admin=True))
    stats = cards.get("/feedback/stats").json()
    # u1: r1+, o1+, e1+ (переоценил) | u2: r1−inaccurate, r2−irrelevant, o1+ | u3: r2−, e1+
    assert stats == {
        "total": 8,
        "useful": 5,
        "not_useful": 3,
        "useful_share": 0.625,
        "by_reason": {"inaccurate": 1, "irrelevant": 1, "unclear": 0, "other": 0, "none": 1},
        "by_type": {
            "risk": {"total": 4, "useful_share": 0.25},
            "opportunity": {"total": 2, "useful_share": 1.0},
            "event": {"total": 2, "useful_share": 1.0},
        },
    }


def test_stats_survive_refresh(cards, db):
    cards.post("/insights/r1/feedback", json={"rating": "useful"})
    with db.begin() as conn:
        conn.execute(text("DELETE FROM insights"))  # refresh пересоздал инсайты
    stats = cards.get("/feedback/stats").json()
    assert stats["total"] == 1 and stats["by_type"]["risk"] == {"total": 1, "useful_share": 1.0}


def test_stats_empty_and_admin_only(cards, as_user):
    stats = cards.get("/feedback/stats").json()  # demo — администратор
    assert stats["total"] == 0 and stats["useful_share"] is None
    as_user(User(id="u1", is_admin=False))
    r = cards.get("/feedback/stats")
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "detail": "Доступно только администратору"}
