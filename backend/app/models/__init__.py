"""Таблицы БД (ORM-модели)."""

from app.models.bank import Company, Payment, Person, PersonCompanyLink, RejectedRow
from app.models.base import Base
from app.models.document import Document
from app.models.profile import CompanyProfileRow

__all__ = [
    "Base",
    "Company",
    "CompanyProfileRow",
    "Document",
    "Payment",
    "Person",
    "PersonCompanyLink",
    "RejectedRow",
]
