from typing import Any

from pydantic import BaseModel, Field


class Company(BaseModel):
    id: str
    name: str
    industry: str
    region: str
    revenue_bln_rub: float


class CompanyCreate(BaseModel):
    """Данные для создания компании (id назначает сервер)."""

    name: str
    industry: str
    region: str
    revenue_bln_rub: float


class CompanyProfile(BaseModel):
    company_id: str
    industry: str = ""
    regions: list[str] = Field(default_factory=list)
    key_clients: list[str] = Field(default_factory=list)
    key_suppliers: list[str] = Field(default_factory=list)
    raw_materials: list[str] = Field(default_factory=list)
    currency_risks: list[str] = Field(default_factory=list)
    debt_load: str = ""
    strategic_goals: list[str] = Field(default_factory=list)
    bank_products: list[str] = Field(default_factory=list)
    # Поля, которые вводят вручную; пока их нет — пустой объект
    manual_data: dict[str, Any] = Field(default_factory=dict)
