from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CompanySource = Literal["bank", "manual"]
FieldSource = Literal["auto", "manual"]


class Company(BaseModel):
    id: str
    name: str
    industry: str
    region: str
    # Значение как в файле банка (единицы пока не подтверждены); для созданных через POST —
    # как ввёл пользователь. Если revenue_known = false, 0.0 — заглушка, а не настоящий ноль
    revenue_bln_rub: float
    revenue_known: bool = True
    # bank — из данных банка (удалить нельзя), manual — создана через POST /companies
    source: CompanySource = "bank"


class CompanyCreate(BaseModel):
    """Данные для создания компании (id назначает сервер)."""

    name: str
    industry: str
    region: str
    revenue_bln_rub: float


class Counterparty(BaseModel):
    """Контрагент из платежей: покупатель (платит компании) или поставщик (получает от неё)."""

    company_id: str
    name: str
    industry: str = ""
    # сумма платежей как в файле; None — в файле не число
    amount: int | None = None
    # есть ли контрагент в таблице companies (у внешних отрасль неизвестна)
    is_known: bool = False


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
    # --- B3: поля из данных банка (необязательные, могут быть пустыми) ---
    name: str = ""
    okved_code: str = ""
    city: str = ""
    revenue_bln_rub: float = 0.0  # как в Company: значение как в файле, 0.0 при revenue_known=false
    revenue_known: bool = True
    employees: str = ""  # градация численности (ФНС, если нет — СПАРК)
    foreign_trade: bool | None = None  # ВЭД (внешнеэкономическая деятельность): None — неизвестно
    foreign_trade_countries: list[str] = Field(default_factory=list)
    group_id: str = ""
    group_size: int = 0  # компаний в группе, включая эту; 0 — группы нет
    top_buyers: list[Counterparty] = Field(default_factory=list)
    top_suppliers: list[Counterparty] = Field(default_factory=list)
    # Откуда значение поля: auto — посчитано из данных банка, manual — введено вручную
    field_sources: dict[str, FieldSource] = Field(default_factory=dict)


# Поля профиля, у которых нет источника: id из адреса и сама пометка источников
SERVICE_FIELDS = frozenset({"company_id", "field_sources"})
PROFILE_FIELDS = tuple(f for f in CompanyProfile.model_fields if f not in SERVICE_FIELDS)


class CompanyProfileUpdate(CompanyProfile):
    """Тело PUT /companies/{id}/profile.

    Ручными становятся только поля, которые есть в теле (даже пустые, например
    "key_clients": []). Остальные ручные поля сохраняются. company_id и field_sources
    в теле игнорируются. Неизвестное поле или неверный тип — 422.
    """

    model_config = ConfigDict(extra="forbid")

    company_id: str = ""

    def manual_fields(self) -> dict[str, Any]:
        """Поля, переданные явно, в виде JSON для manual_data."""
        sent = self.model_fields_set - SERVICE_FIELDS
        return self.model_dump(mode="json", include=sent)
