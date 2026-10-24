"""Компании (таблица companies): банковские (source='bank') и созданные вручную ('manual')."""

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import Company, CompanyProfileRow


def get_by_id(session: Session, company_id: str) -> Company | None:
    """Компания по SYNTHETIC_U или None, если такой нет."""
    return session.get(Company, company_id)


def company_exists(session: Session, company_id: str) -> bool:
    return bool(session.scalar(select(exists().where(Company.synthetic_u == company_id))))


def _escape_like(value: str) -> str:
    """Символы % и _ в поисковой строке ищутся буквально, а не как шаблон."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_companies(
    session: Session, limit: int = 20, offset: int = 0, q: str | None = None
) -> tuple[list[Company], int]:
    """Страница компаний (по возрастанию id) и общее число найденных.

    q — поиск без учёта регистра по id, ручному названию, отрасли (ОКВЭД) и коду ОКВЭД.
    """
    stmt = select(Company)
    if q:
        pattern = f"%{_escape_like(q)}%"
        manual = CompanyProfileRow.manual_data
        stmt = stmt.outerjoin(
            CompanyProfileRow, CompanyProfileRow.company_id == Company.synthetic_u
        ).where(
            or_(
                *(
                    col.ilike(pattern, escape="\\")
                    for col in (
                        Company.synthetic_u,
                        Company.mainokved_name,
                        Company.mainokved_code,
                        manual["name"].astext,
                        manual["industry"].astext,
                    )
                )
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = stmt.order_by(Company.synthetic_u).limit(limit).offset(offset)
    return list(session.scalars(page)), total


def add(session: Session, company: Company) -> None:
    session.add(company)


def delete(session: Session, company: Company) -> None:
    """Удалить компанию; её профиль удалит БД (ON DELETE CASCADE)."""
    session.delete(company)


def first_of_distinct_industries(session: Session, n: int) -> list[Company]:
    """По одной компании из n разных отраслей (первые 2 знака ОКВЭД), по возрастанию id.

    Для проверки стыка с агентом (scripts/check_ml_contract.py) на непохожих компаниях.
    """
    okved2 = func.split_part(Company.mainokved_code, ".", 1)
    ids = (
        select(func.min(Company.synthetic_u).label("id"))
        .where(Company.mainokved_code.is_not(None), Company.mainokved_code != "")
        .group_by(okved2)
        .subquery()
    )
    stmt = select(Company).join(ids, Company.synthetic_u == ids.c.id).order_by(Company.synthetic_u)
    return list(session.scalars(stmt.limit(n)))
