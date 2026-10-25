"""Профиль компании (модуль B3): автоматическая часть из данных банка и ручная."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CompanyProfileRow(Base):
    """auto_data — снимок, посчитанный из таблиц банка; manual_data — поля, введённые вручную.

    В отличие от таблиц банка здесь есть внешний ключ (FK): профиль бывает только у
    существующей компании и удаляется вместе с ней (ON DELETE CASCADE).
    """

    __tablename__ = "company_profiles"

    company_id: Mapped[str] = mapped_column(
        Text, ForeignKey("companies.synthetic_u", ondelete="CASCADE"), primary_key=True
    )
    auto_data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    manual_data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
