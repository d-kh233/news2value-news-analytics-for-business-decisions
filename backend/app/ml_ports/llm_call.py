"""Вызов модели для функций llm/ вне агента (дайджест, ответ на вопрос).

Тот же шлюз, что у карточек агента (agent/gateway.py): LLM_MODE=stub — модель не вызывается,
live — вызов с дневным лимитом и записью ответа в replay_cache, replay — только из записи.
Если модель недоступна, вызывающий порт отдаёт заглушку (Fake) — см. digest_port и qa_port.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.ml_ports.agent_port import ensure_repo_on_path


def gateway() -> tuple[Callable[..., str], str]:
    """(complete для llm.client.call_json, LLM_MODE)."""
    ensure_repo_on_path()
    import llm.client as llm_client
    from agent.config import Settings
    from agent.gateway import Gateway

    s = Settings.from_env()
    # общий кэш call_json — его же читает replay (как в agent/orchestrator.py)
    llm_client.CACHE_DIR = Path(s.llm_cache_dir)
    return Gateway(s).complete, s.llm_mode


def tokens_spent(complete: Callable[..., str]) -> int:
    """Сколько токенов потратил шлюз, чей complete передали в llm/ (0 — модель не вызывалась)."""
    gw = getattr(complete, "__self__", None)
    return int((getattr(gw, "stats", None) or {}).get("tokens") or 0)


def unavailable_errors() -> tuple[type[Exception], ...]:
    """Ошибки «модель недоступна или ответ не прошёл проверку» — на них порт берёт заглушку."""
    ensure_repo_on_path()
    from agent.gateway import LLMUnavailable
    from llm.client import LLMCallError

    return (LLMUnavailable, LLMCallError)


def company_row(company_id: str) -> dict[str, Any]:
    """Строка профиля компании для промпта (признаки F:/G:); нет в БД — только id."""
    from app.ml_ports.agent_port import row_from_db

    return row_from_db(company_id) or {"SYNTHETIC_U": company_id}
