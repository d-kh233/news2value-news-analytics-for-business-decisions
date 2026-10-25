"""Тестовые пользователи для демо и проверки экспертом (B8+B9).

Пароль у всех один — DEMO_USER_PASSWORD из окружения (в коде паролей нет). Повторный запуск
обновляет пароль, роль и компанию, дублей не создаёт. Пользователь компании, которой нет в
базе, пропускается с пояснением (сначала загрузите CSV: load_csv.py или seed_demo.py).
"""

import os
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import hash_password
from app.repositories import company_repo, user_repo

PASSWORD_ENV = "DEMO_USER_PASSWORD"
MIN_PASSWORD_LEN = 8

# email, роль, компания (демо-компании из data/demo_profiles и data/synthetic)
DEMO_USERS = [
    ("admin@news2value.demo", "admin", None),
    ("owner.stroy@news2value.demo", "owner", "U_67064"),
    ("cfo.stroy@news2value.demo", "cfo", "U_67064"),
    ("treasurer.azs@news2value.demo", "treasurer", "U_19717"),
]


@dataclass
class DemoUsersReport:
    saved: list[tuple[str, str, str | None]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def demo_password() -> str:
    """Пароль из DEMO_USER_PASSWORD; ValueError — не задан или короче 8 символов."""
    password = os.environ.get(PASSWORD_ENV) or get_settings().demo_user_password
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(
            f"Задайте {PASSWORD_ENV} в .env (не короче {MIN_PASSWORD_LEN} символов) — "
            "это пароль тестовых пользователей"
        )
    return password


def create_demo_users(password: str | None = None) -> DemoUsersReport:
    password = password or demo_password()
    password_hash = hash_password(password)
    report = DemoUsersReport()
    with get_session() as session:
        for email, role, company_id in DEMO_USERS:
            if company_id is not None and company_repo.get_by_id(session, company_id) is None:
                report.skipped.append(f"{email}: компании {company_id} нет в базе")
                continue
            user_repo.upsert(session, email, password_hash, role, company_id)
            report.saved.append((email, role, company_id))
        session.commit()
    return report


def format_report(report: DemoUsersReport) -> str:
    lines = [f"Тестовые пользователи (пароль — {PASSWORD_ENV} из .env): {len(report.saved)}"]
    for email, role, company_id in report.saved:
        lines.append(f"  {email:34} {role:10} {company_id or 'все компании'}")
    lines += [f"  пропущен {s}" for s in report.skipped]
    return "\n".join(lines)
