"""Стык бэкенда с агентом (agent/) и LLM-частью (llm/).

Пакеты agent/ и llm/ лежат в корне репозитория. В Docker они копируются в /app рядом с app/
(см. backend/Dockerfile); при локальном запуске из backend/ корень репозитория добавляется в путь.

Профиль компании для агента — строка с колонками df_Uid_data_hack:
  • компания из таблицы companies (SYNTHETIC_U) — строка БД как есть плюс manual_data из
    company_profiles (сырьё, контрагенты, цели из демо-профилей и PUT; банковские поля главнее);
  • моковая или созданная через API компания — поля Company и CompanyProfile, ОКВЭД и флаги
    продуктов берутся из manual_data, а если флагов нет — угадываются по тексту bank_products;
    сырьё, контрагенты и валютные риски из профиля идут в поиск сущностей.

Период: API принимает period = day | week, агент и news_provider — число дней. Переводим здесь
(period_to_days), больше нигде эти два вида периода не встречаются.
"""

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Совпадает с llm/insight.py ROLES (тексты фокуса ролей — там)
ROLES = ("owner", "ceo", "cfo", "treasurer", "accountant", "procurement")

# period из API -> дни для агента и news_provider; без period — прежние 30 дней
PERIOD_DAYS = {"day": 1, "week": 7}
DEFAULT_PERIOD_DAYS = 30


def period_to_days(period: str | None) -> int:
    """Перевод period из API в дни: day -> 1, week -> 7, None -> 30 (как было до B5)."""
    if period is None:
        return DEFAULT_PERIOD_DAYS
    if period not in PERIOD_DAYS:
        raise ValueError(f"period={period!r}: допустимо {' | '.join(PERIOD_DAYS)}")
    return PERIOD_DAYS[period]


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
    _ensure_repo_on_path()
    from agent.profiles import row_from_backend_profile

    return row_from_backend_profile(company, profile)


def row_from_db(company_id: str) -> dict[str, Any] | None:
    """Строка таблицы companies + manual_data профиля или None (нет БД или нет такой компании).

    manual_data агент читает сам (CompanyProfile.from_row): ключи переводит в верхний регистр
    и заполняет ими только пустые банковские поля.
    """
    if not get_settings().database_url:
        return None
    from app.core.db import get_session
    from app.repositories import company_repo, profile_repo

    with get_session() as session:
        c = company_repo.get_by_id(session, company_id)
        if c is None:
            return None
        row: dict[str, Any] = {col.key: getattr(c, col.key) for col in c.__table__.columns}
        profile = profile_repo.get(session, company_id)
        if profile is not None and profile.manual_data:
            row["manual_data"] = dict(profile.manual_data)
        return row


def prompt_version() -> str:
    """Версия промптов LLM-части (llm/backend_adapter.py): по ней сбрасывается кэш llm_cache."""
    _ensure_repo_on_path()
    from llm.backend_adapter import PROMPT_VERSION

    return PROMPT_VERSION


def select_news_provider(news_source: str) -> Callable[[str, int], list[dict]] | None:
    """NEWS_SOURCE: db — новости из таблицы documents (под кэшем llm_cache); mock — None,
    агент берёт свои моки."""
    if news_source == "db":
        from app.ml_ports.news_port import get_relevant_news

        return get_relevant_news
    return None


def generate_insights(
    company_id: str,
    role: str | None = None,
    *,
    company: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    period_days: int = DEFAULT_PERIOD_DAYS,
    period: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Прогон агента. Возвращает (инсайты в схеме backend Insight, сводка прогона с трейсом).

    company/profile — моковая компания; без них компания ищется в таблице companies.
    period — значение из API (day | week); если задан, он главнее period_days.
    """
    if period is not None:
        period_days = period_to_days(period)
    _ensure_repo_on_path()
    from agent.orchestrator import Orchestrator
    from agent.schemas import CompanyProfile

    if company is not None:
        row: dict[str, Any] | None = row_from_profile(company, profile or {})
    else:
        row = row_from_db(company_id)
    if row is None:
        raise LookupError(f"Компания {company_id} не найдена ни в моках, ни в таблице companies")
    orchestrator = Orchestrator(news_provider=select_news_provider(get_settings().news_source))
    result = orchestrator.run(CompanyProfile.from_row(row, company_id), period_days, role=role)
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
