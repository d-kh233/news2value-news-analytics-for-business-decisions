"""Компании и их профили на БД (B3).

Профиль = автоматическая часть (profile_builder, из таблиц банка) + ручная (manual_data).
Если поле есть в обеих, побеждает ручное — даже пустое, переданное явно.
Карточка Company тоже берёт из ручной части название, отрасль, регион и выручку.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError
from app.models import Company as CompanyRow
from app.repositories import company_repo, profile_repo
from app.schemas.company import (
    PROFILE_FIELDS,
    Company,
    CompanyCreate,
    CompanyProfile,
    CompanyProfileUpdate,
)
from app.services import profile_builder


def _require(session: Session, company_id: str) -> CompanyRow:
    company = company_repo.get_by_id(session, company_id)
    if company is None:
        raise NotFoundError(f"Компания {company_id} не найдена")
    return company


def _manual(session: Session, company_id: str) -> dict[str, Any]:
    row = profile_repo.get(session, company_id)
    return dict(row.manual_data) if row is not None else {}


def _card(company: CompanyRow, manual: dict[str, Any]) -> Company:
    """Карточка компании: ручные значения главнее данных банка."""
    regions = manual["regions"] if "regions" in manual else [company.region or ""]
    if "revenue_bln_rub" in manual:
        revenue, known = float(manual["revenue_bln_rub"]), True
    elif company.revenue_company is not None:
        revenue, known = float(company.revenue_company), company.revenue_known
    else:
        revenue, known = 0.0, company.revenue_known
    return Company(
        id=company.synthetic_u,
        name=profile_builder.display_name(company.synthetic_u, manual.get("name")),
        industry=manual.get("industry", company.mainokved_name or ""),
        region=regions[0] if regions else "",
        revenue_bln_rub=revenue,
        revenue_known=known,
        source=company.source,  # type: ignore[arg-type]
    )


def _merge(company_id: str, auto: dict[str, Any], manual: dict[str, Any]) -> CompanyProfile:
    sources = {f: ("manual" if f in manual else "auto") for f in PROFILE_FIELDS}
    data = {**auto, **manual, "company_id": company_id, "field_sources": sources}
    return CompanyProfile.model_validate(data)


def _profile(session: Session, company: CompanyRow) -> CompanyProfile:
    """Посчитать auto, сохранить снимок в auto_data и объединить с ручной частью."""
    auto = profile_builder.build_auto(session, company)
    profile_repo.save_auto(session, company.synthetic_u, auto)
    return _merge(company.synthetic_u, auto, _manual(session, company.synthetic_u))


# ---- компании ----


def company_exists(company_id: str) -> bool:
    with get_session() as session:
        return company_repo.company_exists(session, company_id)


def list_companies(limit: int, offset: int, q: str | None = None) -> tuple[list[Company], int]:
    with get_session() as session:
        rows, total = company_repo.list_companies(session, limit, offset, q)
        manual = profile_repo.manual_data_by_ids(session, (c.synthetic_u for c in rows))
        return [_card(c, manual.get(c.synthetic_u, {})) for c in rows], total


def get_company(company_id: str) -> Company:
    with get_session() as session:
        company = _require(session, company_id)
        return _card(company, _manual(session, company_id))


def create_company(data: CompanyCreate) -> Company:
    """Компания из API: source='manual', выручка известна (введена пользователем).

    Отрасль и регион — в колонках companies (их видит поиск и авто-профиль),
    название и выручка — в ручной части профиля (тип revenue_company — целое число).
    """
    company = CompanyRow(
        synthetic_u=f"c-{uuid.uuid4().hex[:8]}",
        mainokved_name=data.industry,
        region=data.region,
        source="manual",
        revenue_known=True,
    )
    manual = {"name": data.name, "revenue_bln_rub": data.revenue_bln_rub}
    with get_session() as session, session.begin():
        company_repo.add(session, company)
        session.flush()
        profile_repo.save_manual(session, company.synthetic_u, manual)
        return _card(company, manual)


def delete_company(company_id: str) -> None:
    """Удалить можно только компанию, созданную вручную; банковскую — 409."""
    with get_session() as session, session.begin():
        company = _require(session, company_id)
        if company.source != "manual":
            raise ConflictError(
                f"Компания {company_id} загружена из данных банка, удалить её нельзя. "
                "Удаляются только компании, созданные через POST /companies"
            )
        company_repo.delete(session, company)


# ---- профиль ----


def get_profile(company_id: str) -> CompanyProfile:
    with get_session() as session, session.begin():
        return _profile(session, _require(session, company_id))


def put_profile(company_id: str, update: CompanyProfileUpdate) -> CompanyProfile:
    """Поля из тела становятся ручными; ручные поля, которых нет в теле, сохраняются."""
    with get_session() as session, session.begin():
        company = _require(session, company_id)
        manual = {**_manual(session, company_id), **update.manual_fields()}
        profile_repo.save_manual(session, company_id, manual)
        return _profile(session, company)


def replace_manual(session: Session, company_id: str, update: CompanyProfileUpdate) -> None:
    """Заменить ручную часть целиком (демо-профили: файл — источник правды)."""
    _require(session, company_id)
    profile_repo.save_manual(session, company_id, update.manual_fields())
