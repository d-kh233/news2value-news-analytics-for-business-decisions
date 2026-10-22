"""Компании из данных банка (таблица companies)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company


def get_by_id(session: Session, company_id: str) -> Company | None:
    """Компания по SYNTHETIC_U или None, если такой нет."""
    return session.get(Company, company_id)


def list_companies(session: Session, limit: int = 20, offset: int = 0) -> tuple[list[Company], int]:
    """Страница компаний (по возрастанию id) и общее число компаний."""
    total = session.scalar(select(func.count()).select_from(Company)) or 0
    stmt = select(Company).order_by(Company.synthetic_u).limit(limit).offset(offset)
    return list(session.scalars(stmt)), total
