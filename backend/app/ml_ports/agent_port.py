"""Стык бэкенда с агентом (agent/) и LLM-частью (llm/).

Пакеты agent/ и llm/ лежат в корне репозитория. В Docker они копируются в /app рядом с app/
(см. backend/Dockerfile); при локальном запуске из backend/ корень репозитория добавляется в путь.

Профиль компании для агента — строка с колонками df_Uid_data_hack:
  • компания из таблицы companies (SYNTHETIC_U) — строка БД как есть;
  • моковая или созданная через API компания — поля Company и CompanyProfile, ОКВЭД и флаги
    продуктов берутся из manual_data, а если флагов нет — угадываются по тексту bank_products.
"""

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Совпадает с llm/insight.py ROLES (тексты фокуса ролей — там)
ROLES = ("owner", "ceo", "cfo", "treasurer", "accountant", "procurement")

# Подсказки для профилей, введённых вручную: слово в bank_products -> флаг продукта из CSV банка
_PRODUCT_HINTS = {
    "CL_PRODUCT_LOA_MARK": ("кредит", "овердрафт", "займ"),
    "CL_PRODUCT_LEA_MARK": ("лизинг",),
    "CL_PRODUCT_DEP_MARK": ("депозит",),
    "CL_PRODUCT_ZPP_MARK": ("зарплат",),
    "CL_PRODUCT_GAR_MARK": ("гарант",),
    "FACTORING_MARK": ("факторинг",),
}
_FX_HINTS = ("экспорт", "импорт", "юан", "доллар", "евро", "валют")


def _ensure_repo_on_path() -> None:
    here = Path(__file__).resolve()
    for root in here.parents[2:4]:
        if (root / "agent").is_dir() and (root / "llm").is_dir():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return
    raise ImportError("Не найдены пакеты agent/ и llm/ рядом с бэкендом")


def row_from_profile(company: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Строка в формате df_Uid_data_hack из Company + CompanyProfile (моки и ручной ввод)."""
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
    row.update({k: v for k, v in manual.items() if v not in (None, "")})  # ручные данные главнее
    return row


def row_from_db(company_id: str) -> dict[str, Any] | None:
    """Строка таблицы companies или None (нет БД или нет такой компании)."""
    from app.core.config import get_settings

    if not get_settings().database_url:
        return None
    from app.core.db import get_session
    from app.repositories import company_repo

    with get_session() as session:
        c = company_repo.get_by_id(session, company_id)
        if c is None:
            return None
        return {col.key: getattr(c, col.key) for col in c.__table__.columns}


def generate_insights(
    company_id: str,
    role: str | None = None,
    *,
    company: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    period_days: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Прогон агента. Возвращает (инсайты в схеме backend Insight, сводка прогона с трейсом).

    company/profile — моковая компания; без них компания ищется в таблице companies.
    """
    _ensure_repo_on_path()
    from agent.orchestrator import Orchestrator
    from agent.schemas import CompanyProfile

    if company is not None:
        row: dict[str, Any] | None = row_from_profile(company, profile or {})
    else:
        row = row_from_db(company_id)
    if row is None:
        raise LookupError(f"Компания {company_id} не найдена ни в моках, ни в таблице companies")
    result = Orchestrator().run(CompanyProfile.from_row(row, company_id), period_days, role=role)
    logger.info(
        "agent run %s: %d инсайтов, LLM %s", result.run_id, len(result.insights), result.llm_mode
    )
    summary = {
        "run_id": result.run_id,
        "role": result.role,
        "llm_mode": result.llm_mode,
        "plan": result.plan,
        "trace": [t.model_dump(mode="json") for t in result.trace],
        "llm_usage": {k: v for k, v in result.llm_usage.items() if k != "calls_log"},
    }
    return result.insights, summary
