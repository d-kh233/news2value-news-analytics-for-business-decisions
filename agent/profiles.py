"""Профили компаний для запуска агента без бэкенда (CLI, тесты).

В бэкенде профиль приходит из таблицы companies или из моков (backend/app/ml_ports/agent_port.py),
сюда он не заглядывает. Формат CSV — как df_Uid_data_hack: разделитель «;», id в SYNTHETIC_U,
пробелы вокруг значений бывают. Колонки берутся как есть и приводятся к верхнему регистру.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Iterable, Optional

from .schemas import CompanyProfile


def _read_text(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_csv(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    text = _read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    return [h.strip() for h in (reader.fieldnames or [])], rows


def detect_csv_kind(header: Iterable[str]) -> str:
    """Разводит файлы кейса (два из них в ТЗ названы одинаково)."""
    cols = {h.strip().upper() for h in header}
    if "PAY_AMT" in cols:
        return "payments"
    if "CITY_NAME" in cols or "SYNTHETIC_F" in cols:
        return "links"
    if "MAINOKVED_CODE" in cols or "SYNTHETIC_U" in cols:
        return "companies"
    return "unknown"


class ProfileStore:
    def __init__(self, companies_csv: str):
        self.companies_csv = companies_csv
        header, rows = read_csv(companies_csv)
        kind = detect_csv_kind(header)
        if kind != "companies":
            raise ValueError(f"{companies_csv}: это не файл компаний (определён как {kind!r})")
        self.header = header
        self._profiles: dict[str, CompanyProfile] = {}
        for r in rows:
            if not (r.get("SYNTHETIC_U") or "").strip():
                continue
            p = CompanyProfile.from_row(r)
            self._profiles[p.company_id] = p      # дубль id — берём последнюю строку (бэкенд их отбраковывает)

    def get(self, company_id: str) -> Optional[CompanyProfile]:
        return self._profiles.get(company_id)

    def list_brief(self, limit: int = 100) -> list[dict]:
        return [{"company_id": p.company_id, "industry": p.industry, "okved": p.okved, "region": p.region}
                for p in list(self._profiles.values())[:limit]]

    def describe(self) -> dict:
        """Что увидит модель по каждой колонке. Запустить на реальном файле: python -m agent inspect-csv"""
        sample = next(iter(self._profiles.values()), None)
        return {"n_companies": len(self._profiles), "header": self.header,
                "facts_of_first": sample.facts() if sample else {},
                "okved_not_in_map": sorted({p.okved or "—" for p in self._profiles.values() if not p.okved2})[:20]}


# ---------- профиль из бэкенда (моки и ручной ввод через API) ----------

# слово в bank_products -> флаг продукта из CSV банка
_PRODUCT_HINTS = {
    "CL_PRODUCT_LOA_MARK": ("кредит", "овердрафт", "займ"),
    "CL_PRODUCT_LEA_MARK": ("лизинг",),
    "CL_PRODUCT_DEP_MARK": ("депозит",),
    "CL_PRODUCT_ZPP_MARK": ("зарплат",),
    "CL_PRODUCT_GAR_MARK": ("гарант",),
    "FACTORING_MARK": ("факторинг",),
}
_FX_HINTS = ("экспорт", "импорт", "юан", "доллар", "евро", "валют")
_PROFILE_LISTS = ("raw_materials", "key_clients", "key_suppliers", "currency_risks", "strategic_goals")


def row_from_backend_profile(company: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Строка в формате df_Uid_data_hack из backend Company + CompanyProfile.

    ОКВЭД и флаги продуктов — из manual_data; если флагов нет, они угадываются по тексту bank_products,
    ВЭД — по валютным рискам. Списки профиля (сырьё, контрагенты, валюты) идут в поиск сущностей.
    """
    manual = {k.upper(): v for k, v in (profile.get("manual_data") or {}).items()}
    products = " ".join(profile.get("bank_products") or []).lower()
    fx = " ".join(profile.get("currency_risks") or []).lower()
    row: dict[str, Any] = {
        "SYNTHETIC_U": company["id"],
        "MAINOKVED_NAME": profile.get("industry") or company.get("industry"),
        "REGION": company.get("region"),
    }
    for flag, words in _PRODUCT_HINTS.items():
        row[flag] = 1 if any(w in products for w in words) else 0
    row["FTS_FLAG"] = 1 if any(w in fx for w in _FX_HINTS) else 0
    for field in _PROFILE_LISTS:
        if profile.get(field):
            row[field.upper()] = list(profile[field])
    row.update({k: v for k, v in manual.items() if v not in (None, "")})  # ручные данные главнее
    return row
