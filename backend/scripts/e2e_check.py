"""Сквозная проверка работающего API (B8+B9): как пройдёт сценарий эксперт.

вход → список компаний → профиль → refresh → ожидание задачи → лента → карточка →
трассировка → дайджест. На каждом шаге — OK или FAIL с причиной. После первого FAIL
зависимые шаги не выполняются (SKIP). В базу пишет только то, что пишет сам сценарий (refresh).

Запуск из backend/ (API уже поднят: docker compose up, затем seed_demo.py):
    uv run python scripts/e2e_check.py                                # http://localhost:8000
    uv run python scripts/e2e_check.py --base-url https://news2value.example.ru
Внутри контейнера api:
    docker compose exec api python scripts/e2e_check.py

Вход: E2E_EMAIL (по умолчанию cfo.stroy@news2value.demo) и E2E_PASSWORD (по умолчанию
DEMO_USER_PASSWORD из окружения или .env). При AUTH_ENABLED=false токен не нужен — если войти
не вышло, шаг «вход» всё равно OK с пояснением. При AUTH_ENABLED=true дополнительно
проверяется, что без токена API отвечает 401, а чужая компания — 403.

Код выхода: 0 — все шаги OK, 1 — есть FAIL.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_EMAIL = "cfo.stroy@news2value.demo"
DEMO_COMPANIES = ("U_67064", "U_19717", "U_16346")


class StepFailed(Exception):
    pass


def _password() -> str:
    value = os.environ.get("E2E_PASSWORD") or os.environ.get("DEMO_USER_PASSWORD")
    if value:
        return value
    try:  # .env рядом с репозиторием (локальный запуск)
        from app.core.config import get_settings

        return get_settings().demo_user_password
    except Exception:  # noqa: BLE001 — без настроек просто нет пароля
        return ""


class Check:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0)
        self.job_timeout = timeout
        self.headers: dict[str, str] = {}
        self.ok = True
        self.failed = False
        self.state: dict[str, Any] = {}

    def step(self, title: str, fn: Callable[[], str]) -> None:
        if self.failed:
            print(f"[SKIP] {title}")
            return
        try:
            detail = fn()
            print(f"[OK]   {title} — {detail}")
        except StepFailed as exc:
            self.failed, self.ok = True, False
            print(f"[FAIL] {title} — {exc}")
        except httpx.HTTPError as exc:
            self.failed, self.ok = True, False
            print(f"[FAIL] {title} — нет связи с API: {type(exc).__name__}: {exc}")

    def get(self, path: str, expect: int = 200, **kw: Any) -> Any:
        r = self.http.get(path, headers=self.headers, **kw)
        return self._check(r, expect, f"GET {path}")

    def post(self, path: str, expect: int, **kw: Any) -> Any:
        r = self.http.post(path, headers=self.headers, **kw)
        return self._check(r, expect, f"POST {path}")

    @staticmethod
    def _check(r: httpx.Response, expect: int, what: str) -> Any:
        if r.status_code != expect:
            try:
                body = r.json()
                reason = f"{body.get('error')}: {body.get('detail')}"
            except ValueError:
                reason = r.text[:200]
            raise StepFailed(f"{what} → {r.status_code}, ждали {expect} ({reason})")
        return r.json() if r.content else None

    # ---------- шаги ----------

    def health(self) -> str:
        body = self.get("/health")
        if body.get("status") != "ok":
            raise StepFailed(f"/health ответил {body}")
        return "API отвечает"

    def login(self, email: str, password: str) -> str:
        me = self.http.get("/auth/me")
        auth_enabled = me.status_code == 401 or (me.json() or {}).get("auth_enabled", False)
        self.state["auth_enabled"] = auth_enabled
        r = self.http.post("/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            body = r.json()
            self.headers = {"Authorization": f"Bearer {body['access_token']}"}
            self.state["user"] = body["user"]
            mode = "вход включён" if auth_enabled else "вход выключен, токен не проверяется"
            user = body["user"]
            return f"{email}: роль {user['role']}, компания {user['company_id']} ({mode})"
        if not auth_enabled:
            return f"AUTH_ENABLED=false — работаем без токена (вход ответил {r.status_code})"
        raise StepFailed(
            f"POST /auth/login → {r.status_code}: проверьте E2E_EMAIL/E2E_PASSWORD "
            "(create_demo_users.py) и JWT_SECRET на сервере"
        )

    def no_token_401(self) -> str:
        r = self.http.get("/companies")
        if r.status_code != 401:
            raise StepFailed(f"без токена GET /companies → {r.status_code}, ждали 401")
        return "без токена — 401"

    def companies(self, wanted: str | None) -> str:
        body = self.get("/companies", params={"limit": 100})
        ids = [c["id"] for c in body["items"]]
        if not ids:
            raise StepFailed("компаний нет — запустите seed_demo.py")
        # демо-компании в списке (по id) могут быть дальше первой сотни — спрашиваем напрямую
        candidates = [wanted] if wanted else [*DEMO_COMPANIES, ids[0]]
        company = next(
            (
                c
                for c in candidates
                if self.http.get(f"/companies/{c}", headers=self.headers).status_code == 200
            ),
            None,
        )
        if company is None:
            raise StepFailed(f"компании {wanted} нет или к ней нет доступа")
        self.state["company"] = company
        return f"всего {body['total']}, проверяю на {company}"

    def foreign_403(self) -> str:
        user = self.state.get("user") or {}
        other = next((c for c in DEMO_COMPANIES if c != user.get("company_id")), None)
        r = self.http.get(f"/companies/{other}/profile", headers=self.headers)
        if r.status_code != 403:
            raise StepFailed(f"чужая компания {other} → {r.status_code}, ждали 403")
        return f"чужая компания {other} — 403"

    def profile(self) -> str:
        p = self.get(f"/companies/{self.state['company']}/profile")
        return f"{p.get('name') or p.get('company_id')}, отрасль: {p.get('industry') or '—'}"

    def refresh(self, role: str | None) -> str:
        params = {"role": role} if role else {}
        body = self.post(f"/companies/{self.state['company']}/insights/refresh", 202, params=params)
        self.state["job"] = body["job_id"]
        return f"задача {body['job_id']}"

    def wait_job(self) -> str:
        job_id = self.state["job"]
        deadline = time.monotonic() + self.job_timeout
        status = "pending"
        while time.monotonic() < deadline:
            job = self.get(f"/jobs/{job_id}")
            status = job["status"]
            if status == "done":
                return f"done за {self.job_timeout - (deadline - time.monotonic()):.0f} с"
            if status == "failed":
                raise StepFailed(f"задача failed: {job.get('error')}")
            time.sleep(2)
        raise StepFailed(
            f"задача {job_id} всё ещё {status} через {self.job_timeout:g} с — запущен ли worker?"
        )

    def feed(self) -> str:
        body = self.get(f"/companies/{self.state['company']}/insights", params={"limit": 100})
        if not body["items"]:
            raise StepFailed(f"лента пуста: {body.get('hint')}")
        self.state["insight"] = body["items"][0]["id"]
        by = {}
        for i in body["items"]:
            by[i.get("generated_by") or "—"] = by.get(i.get("generated_by") or "—", 0) + 1
        return f"карточек {body['total']}, generated_by: {by}"

    def card(self) -> str:
        c = self.get(f"/insights/{self.state['insight']}")
        if not c.get("title") or not c.get("sources"):
            raise StepFailed("у карточки нет заголовка или источников")
        return f"«{c['title'][:60]}», источников {len(c['sources'])}"

    def trace(self) -> str:
        t = self.get(f"/insights/{self.state['insight']}/trace")
        if not t.get("steps"):
            raise StepFailed("в трассировке нет шагов")
        if not t.get("sources"):
            raise StepFailed("в трассировке нет источников")
        return (
            f"запуск {t['run_id']}, шагов {len(t['steps'])}, источников {len(t['sources'])}, "
            f"generated_by={t['generated_by']}, {t.get('duration_ms')} мс"
        )

    def digest(self) -> str:
        d = self.get(f"/companies/{self.state['company']}/digest", params={"period": "week"})
        if not d.get("summary"):
            raise StepFailed("пустой текст дайджеста")
        return f"инсайтов {len(d['insight_ids'])}: {d['summary'][:80]}…"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Сквозная проверка API")
    parser.add_argument(
        "--base-url", default=os.environ.get("E2E_BASE_URL", "http://localhost:8000")
    )
    parser.add_argument("--company", help="id компании (по умолчанию — первая демо-компания)")
    parser.add_argument("--role", help="роль для refresh (по умолчанию — роль пользователя)")
    parser.add_argument("--timeout", type=float, default=180, help="сколько ждать задачу, с")
    args = parser.parse_args()
    email = os.environ.get("E2E_EMAIL", DEFAULT_EMAIL)

    c = Check(args.base_url, args.timeout)
    print(f"Проверяю {args.base_url}")
    c.step("API доступен", c.health)
    c.step("вход", lambda: c.login(email, _password()))
    if c.state.get("auth_enabled"):
        c.step("без токена — 401", c.no_token_401)
    c.step("список компаний", lambda: c.companies(args.company))
    user = c.state.get("user") or {}
    if c.state.get("auth_enabled") and user and user.get("role") != "admin":
        c.step("чужая компания — 403", c.foreign_403)
    c.step("профиль", c.profile)
    c.step("refresh", lambda: c.refresh(args.role))
    c.step("ожидание задачи", c.wait_job)
    c.step("лента", c.feed)
    c.step("карточка", c.card)
    c.step("трассировка", c.trace)
    c.step("дайджест", c.digest)
    print("\nИтог: " + ("всё OK" if c.ok else "есть FAIL — см. выше"))
    return 0 if c.ok else 1


if __name__ == "__main__":
    sys.exit(main())
