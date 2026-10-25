"""Обновление инсайтов компании из БД (B5): вызвать генератор, сохранить итог, записать запуск.

Многошаговую оркестрацию делает агент (agent/, зона тимлида), здесь её нет. refresh только:
  1. создаёт запись pipeline_runs (status=running);
  2. вызывает генератор по ML_MODE (ml_ports/loader): real — агент через real.py (таймаут,
     повтор, MLError), fake — fake_port (заглушка);
  3. проверяет карточки схемой Insight и находит в них ссылки на документы (N:doc-<id>);
  4. в одной транзакции удаляет старые инсайты компании и сохраняет новые;
  5. пишет в pipeline_runs статус и шаги — трейс, который прислал генератор, как есть, —
     и сводку прогона (run_id генератора, роль, режим LLM, план) для GET /jobs/{id}/run.

Статусы: done — инсайты заменены; empty — генератор отработал, но карточек нет (например,
новости не размечены): старые инсайты остаются, причина — в error; failed — ошибка
генератора, проверки или сохранения: старые инсайты остаются, текст ошибки — в error.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import NotFoundError
from app.ml_ports import loader
from app.repositories import document_repo, insight_repo, pipeline_run_repo
from app.schemas.insight import Insight
from app.services import company_service

logger = logging.getLogger(__name__)

ERROR_MAX_LEN = 2000
# поля сводки генератора, которые хранятся в pipeline_runs.summary (трейс — в steps)
SUMMARY_KEYS = ("run_id", "role", "llm_mode", "plan", "llm_usage")
_DOC_REF = re.compile(r"N:doc-(\d+)\b")


@dataclass
class RefreshResult:
    run_id: int
    status: str  # done | empty | failed
    insights: list[Insight] = field(default_factory=list)
    document_ids: dict[str, list[int]] = field(default_factory=dict)  # id инсайта -> документы
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def document_ids(card: dict[str, Any]) -> list[int]:
    """id документов, на которые ссылается карточка: N:doc-<id> в details и в sources.

    У агента источник новости — N:doc-<id> в блоках details (sources id не несут), связанные
    новости повторного поиска — там же; у заглушки — в sources[].id. Моковые новости (N:M01)
    документами не считаются.
    """
    refs = json.dumps([card.get("details"), card.get("sources")], ensure_ascii=False)
    return sorted({int(x) for x in _DOC_REF.findall(refs)})


def _jsonable(steps: Any) -> list[dict[str, Any]]:
    """Трейс в вид, который точно ляжет в JSONB (даты и прочее — строками)."""
    return json.loads(json.dumps(list(steps or []), ensure_ascii=False, default=str))


def _summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Сводка генератора без трейса — в вид для JSONB."""
    return _jsonable([{k: summary.get(k) for k in SUMMARY_KEYS}])[0]


def _empty_reason(steps: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    """Пояснение для status=empty: режимы и заметки шагов, которые пропущены или откатились."""
    s = get_settings()
    modes = f"ML_MODE={s.ml_mode}, NEWS_SOURCE={s.news_source}"
    if s.ml_mode == "real":
        modes += f", LLM_MODE={summary.get('llm_mode')}"
    head = f"Генератор не вернул ни одной карточки ({modes})"
    notes = [
        f"{st.get('name')}: {st.get('note')}"
        for st in steps
        if st.get("status") in ("skipped", "fallback", "error") and st.get("note")
    ]
    return "; ".join([head, *notes])[:ERROR_MAX_LEN]


def _validate(company_id: str, cards: list[dict[str, Any]]) -> list[tuple[Insight, list[int]]]:
    """Карточки -> Insight + ссылки на документы. Повтор id в одном прогоне — берём первую."""
    out: list[tuple[Insight, list[int]]] = []
    seen: set[str] = set()
    for card in cards:
        insight = Insight.model_validate(card)
        if insight.company_id != company_id:
            raise ValueError(f"карточка {insight.id} про компанию {insight.company_id}")
        if insight.id in seen:
            continue
        seen.add(insight.id)
        out.append((insight, document_ids(card)))
    return out


def _finish(
    run_id: int,
    status: str,
    steps: list[dict[str, Any]],
    error: str | None,
    summary: dict[str, Any] | None,
) -> None:
    with get_session() as session:
        pipeline_run_repo.finish(session, run_id, status, steps, error, summary)
        session.commit()


def run_for_company(
    company_id: str,
    role: str | None = None,
    period: str | None = "week",
    on_start: Callable[[int], None] | None = None,
) -> RefreshResult:
    """Обновить инсайты компании из таблицы companies.

    role — роль читателя (None — роль агента по умолчанию), period — day | week (в дни
    переводит agent_port). on_start(run_id) вызывается сразу после создания записи
    pipeline_runs (worker записывает run_id в задачу). Ошибки генератора и сохранения не
    выбрасываются, а попадают в pipeline_runs и в RefreshResult со status=failed.
    NotFoundError — компании нет в БД.
    """
    if not company_service.company_exists(company_id):
        raise NotFoundError(f"Компания {company_id} не найдена")
    settings = get_settings()
    with get_session() as session:
        run_id = pipeline_run_repo.create(session, company_id)
        session.commit()
    if on_start is not None:
        on_start(run_id)

    steps: list[dict[str, Any]] = []
    run_summary: dict[str, Any] | None = None
    try:
        # функции берём при вызове по ML_MODE (loader): в тестах их подменяют
        cards, summary = loader.get_ports().generate_insights(company_id, role, period=period)
        steps = _jsonable(summary.get("trace"))
        run_summary = _summary(summary)
        validated = _validate(company_id, cards)
    except Exception as exc:  # noqa: BLE001 — любой сбой генератора превращаем в статус запуска
        logger.exception("refresh %s: генератор не смог сделать инсайты", company_id)
        error = f"{type(exc).__name__}: {exc}"[:ERROR_MAX_LEN]
        _finish(run_id, "failed", steps, error, run_summary)
        return RefreshResult(run_id, "failed", steps=steps, error=error)

    if not validated:
        reason = _empty_reason(steps, summary)
        logger.warning("refresh %s: %s", company_id, reason)
        _finish(run_id, "empty", steps, reason, run_summary)
        return RefreshResult(run_id, "empty", steps=steps, error=reason)

    generated_by = "fake" if settings.ml_mode == "fake" else None
    try:
        with get_session() as session:
            all_ids = sorted({i for _, ids in validated for i in ids})
            known = document_repo.existing_ids(session, all_ids)
            if missing := sorted(set(all_ids) - known):
                logger.warning(
                    "refresh %s: ссылки на несуществующие документы %s", company_id, missing
                )
            doc_ids = {ins.id: [i for i in ids if i in known] for ins, ids in validated}
            rows = [
                {
                    "id": ins.id,
                    "document_ids": doc_ids[ins.id],
                    "payload": ins.model_dump(mode="json"),
                    "type": ins.type,
                    "importance": ins.importance,
                    "role": ins.role,
                    "generated_by": generated_by or ins.generated_by or "rules",
                    "prompt_version": ins.prompt_version,
                    "created_at": ins.created_at,
                    "run_id": run_id,
                }
                for ins, _ in validated
            ]
            insight_repo.replace_for_company(session, company_id, rows)
            pipeline_run_repo.finish(session, run_id, "done", steps, summary=run_summary)
            session.commit()
    except Exception as exc:  # noqa: BLE001 — транзакция откатилась, старые инсайты на месте
        logger.exception("refresh %s: не удалось сохранить инсайты", company_id)
        error = f"Не удалось сохранить инсайты: {type(exc).__name__}: {exc}"[:ERROR_MAX_LEN]
        _finish(run_id, "failed", steps, error, run_summary)
        return RefreshResult(run_id, "failed", steps=steps, error=error)

    insights = [ins for ins, _ in validated]
    return RefreshResult(run_id, "done", insights, doc_ids, steps)
