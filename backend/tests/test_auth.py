"""Вход и права (B8+B9): AUTH_ENABLED, POST /auth/login, 401 / 403, admin видит всё."""

import pytest
from sqlalchemy import text

from app.core.db import get_session
from app.core.security import TokenError, create_token, decode_token, hash_password
from app.repositories import job_repo, user_repo

PASSWORD = "secret-pass-1"
SECRET = "test-jwt-secret-0123456789"


def add_user(email: str, role: str, company_id: str | None) -> int:
    with get_session() as session:
        user_id = user_repo.upsert(session, email, hash_password(PASSWORD), role, company_id)
        session.commit()
    return user_id


@pytest.fixture
def auth(env, bank_client):
    """AUTH_ENABLED=true, пользователи: admin, cfo и owner компании U_2."""
    env(AUTH_ENABLED="true", JWT_SECRET=SECRET)
    add_user("admin@demo.local", "admin", None)
    add_user("cfo@demo.local", "cfo", "U_2")
    add_user("Owner@Demo.local", "owner", "U_2")
    return bank_client


def login(client, email: str, password: str = PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def headers(client, email: str) -> dict[str, str]:
    r = login(client, email)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def audit_rows(db, action: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM audit_log WHERE action = :a ORDER BY id"), {"a": action}
        )
        return [dict(r._mapping) for r in rows]


# ---- выключено: всё как раньше ----


def test_disabled_works_without_token(bank_client):
    assert bank_client.get("/companies").status_code == 200
    assert bank_client.get("/companies/U_2/profile").status_code == 200
    assert bank_client.get("/documents").status_code == 200
    me = bank_client.get("/auth/me").json()
    assert me["id"] == "demo" and me["is_admin"] is True and me["auth_enabled"] is False


# ---- включено ----


@pytest.mark.parametrize(
    "path",
    ["/companies", "/companies/U_2", "/companies/U_2/profile", "/companies/U_2/insights",
     "/companies/U_2/digest?period=week", "/insights/x", "/insights/x/trace", "/jobs/x",
     "/documents", "/feedback/stats", "/auth/me"],
)  # fmt: skip
def test_no_token_401(auth, path):
    r = auth.get(path)
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_health_and_docs_are_open(auth):
    assert auth.get("/health").status_code == 200
    assert auth.get("/openapi.json").status_code == 200


def test_bad_token_401(auth):
    r = auth.get("/companies", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_login_and_me(auth):
    r = login(auth, "CFO@demo.local ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] > 0
    assert body["user"]["role"] == "cfo" and body["user"]["company_id"] == "U_2"
    me = auth.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == "cfo@demo.local"
    assert me.json()["auth_enabled"] is True


def test_wrong_password_and_unknown_email_same_401(auth):
    a = login(auth, "cfo@demo.local", "wrong")
    b = login(auth, "nobody@demo.local", "wrong")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_login_written_to_audit(auth, db):
    login(auth, "cfo@demo.local")
    login(auth, "nobody@example.com", "wrong")
    rows = audit_rows(db, "login")
    assert [r["status"] for r in rows] == ["ok", "failed"]
    assert rows[0]["user_id"] and rows[0]["company_id"] == "U_2"
    assert rows[1]["user_id"] is None
    assert rows[1]["input_refs"] == {"email": "n***@e***.com"}
    assert "nobody" not in str(rows[1])


def test_company_user_sees_only_own_company(auth):
    h = headers(auth, "cfo@demo.local")
    ids = [c["id"] for c in auth.get("/companies", headers=h).json()["items"]]
    assert ids == ["U_2"]
    assert auth.get("/companies/U_2/profile", headers=h).status_code == 200
    assert auth.get("/companies/U_2/insights", headers=h).status_code == 200
    for path in ("/companies/U_4", "/companies/U_4/profile", "/companies/U_4/insights"):
        r = auth.get(path, headers=h)
        assert r.status_code == 403, path
        assert r.json()["error"] == "forbidden"
    assert auth.post("/companies/U_4/insights/refresh", headers=h).status_code == 403


def test_two_users_of_one_company(auth):
    for email in ("cfo@demo.local", "owner@demo.local"):
        h = headers(auth, email)
        assert auth.get("/companies/U_2/profile", headers=h).status_code == 200


def test_service_endpoints_admin_only(auth):
    h = headers(auth, "cfo@demo.local")
    assert auth.get("/documents", headers=h).status_code == 403
    assert auth.get("/feedback/stats", headers=h).status_code == 403
    new = {"name": "ООО Тест", "industry": "Торговля", "region": "Москва", "revenue_bln_rub": 1}
    assert auth.post("/companies", json=new, headers=h).status_code == 403
    admin = headers(auth, "admin@demo.local")
    assert auth.get("/documents", headers=admin).status_code == 200
    assert auth.get("/feedback/stats", headers=admin).status_code == 200
    assert len(auth.get("/companies", headers=admin).json()["items"]) > 1
    assert auth.get("/companies/U_4/profile", headers=admin).status_code == 200


def test_refresh_takes_user_role_and_id(auth):
    h = headers(auth, "cfo@demo.local")
    r = auth.post("/companies/U_2/insights/refresh", headers=h)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    with get_session() as session:
        job = job_repo.get(session, job_id)
        assert job.params["role"] == "cfo"
        assert job.params["user_id"]
    # своя задача видна, чужому пользователю — 403
    assert auth.get(f"/jobs/{job_id}", headers=h).status_code == 200
    add_user("other@demo.local", "ceo", "U_4")
    other = headers(auth, "other@demo.local")
    assert auth.get(f"/jobs/{job_id}", headers=other).status_code == 403


def test_deleted_user_token_401(auth, db):
    h = headers(auth, "cfo@demo.local")
    with db.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email = 'cfo@demo.local'"))
    assert auth.get("/companies", headers=h).status_code == 401


def test_token_signed_with_other_secret_rejected(env):
    env(JWT_SECRET=SECRET)
    token, _ = create_token(1)
    assert decode_token(token) == 1
    env(JWT_SECRET="another-secret-0123456789")
    with pytest.raises(TokenError):
        decode_token(token)


def test_login_without_secret_503(env, bank_client):
    env(JWT_SECRET="")
    add_user("cfo@demo.local", "cfo", "U_2")
    r = login(bank_client, "cfo@demo.local")
    assert r.status_code == 503
    assert r.json()["error"] == "service_unavailable"
