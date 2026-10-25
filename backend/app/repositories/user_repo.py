"""Пользователи (таблица users)."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import UserRow


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get(session: Session, user_id: int) -> UserRow | None:
    return session.get(UserRow, user_id)


def get_by_email(session: Session, email: str) -> UserRow | None:
    return session.scalar(select(UserRow).where(UserRow.email == normalize_email(email)))


def upsert(
    session: Session, email: str, password_hash: str, role: str, company_id: str | None
) -> int:
    """Создать пользователя или обновить пароль, роль и компанию существующего; вернуть id."""
    values = {
        "email": normalize_email(email),
        "password_hash": password_hash,
        "role": role,
        "company_id": company_id,
    }
    stmt = insert(UserRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserRow.email],
        set_={k: stmt.excluded[k] for k in ("password_hash", "role", "company_id")},
    ).returning(UserRow.id)
    return session.scalar(stmt)  # type: ignore[return-value]
