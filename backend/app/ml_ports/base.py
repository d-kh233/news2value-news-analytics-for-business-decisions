"""Интерфейсы стыка с ML-частью (B8+B9): какие функции бэкенд ждёт от ML и в какой форме.

ML-код живёт в agent/ (агент) и llm/ (модель, дайджест, ответ на вопрос). Реализации:
  • fake — заглушки без agent/ и llm/ (fake_port, ветки fake в digest_port / qa_port);
  • real — адаптеры real.py: те же порты, плюс таймаут, один повтор и единая ошибка MLError.
Какую взять, решает loader.get_ports() по ML_MODE. Проверка соответствия —
scripts/check_ml_contract.py.

Protocol — описание формы функции (имена и виды параметров, что возвращает), без реализации.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol


class MLError(RuntimeError):
    """Любая ошибка ML-функции в режиме real: таймаут, сбой агента или модели, лимит токенов.

    Текст — для человека: какая функция, сколько попыток, что случилось.
    """


class InsightGenerator(Protocol):
    """Прогон генератора инсайтов компании.

    role — роль читателя (None — по умолчанию генератора), period — day | week (в дни переводит
    agent_port.period_to_days). Возвращает (карточки в схеме app/schemas/insight.Insight,
    сводка прогона: run_id, role, llm_mode, plan, trace — список шагов, llm_usage).
    """

    def __call__(
        self, company_id: str, role: str | None = None, *, period: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class DigestWriter(Protocol):
    """Текст дайджеста: items — [[id инсайта, важность], ...], самые важные первыми.

    Возвращает {"summary": str, "generated_by": "llm" | "fake" | "fallback"}.
    """

    def __call__(self, company_id: str, period: str, items: list[list[Any]]) -> dict[str, str]: ...


class QuestionAnswerer(Protocol):
    """Ответ на вопрос по карточке инсайта; history — [{question, answer}], старые первыми.

    Возвращает {"answer": str, "sources": [Source], "generated_by": "llm" | "fake" | "fallback"}.
    """

    def __call__(
        self, insight: dict[str, Any], question: str, history: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


# Источник новостей для агента (agent/tools.py NewsProvider): (company_id, дни) -> новости
NewsProvider = Callable[[str, int], list[dict[str, Any]]]


@dataclass(frozen=True)
class MLPorts:
    """Набор ML-функций для текущего ML_MODE (loader.get_ports)."""

    mode: Literal["fake", "real"]
    generate_insights: InsightGenerator
    digest_summary: DigestWriter
    answer_question: QuestionAnswerer
