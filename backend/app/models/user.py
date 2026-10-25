"""Пользователи (B8+B9): вход по email и паролю, роль и компания."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# роли руководителей совпадают с ролями читателя инсайта (refresh без role берёт роль пользователя)
USER_ROLES = ("owner", "ceo", "cfo", "treasurer", "admin")


class UserRow(Base):
    """Пользователь. password_hash — bcrypt (сам пароль не хранится).

    company_id — компания, инсайты которой пользователь видит; у admin может быть пустым
    (admin видит все компании). В одной компании может быть несколько пользователей.
    Компания удалена — удаляются и её пользователи (ON DELETE CASCADE).
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN {USER_ROLES}", name="ck_users_role"),
        CheckConstraint("role = 'admin' OR company_id IS NOT NULL", name="ck_users_company"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # всегда в нижнем регистре (user_repo)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(12))
    company_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("companies.synthetic_u", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
