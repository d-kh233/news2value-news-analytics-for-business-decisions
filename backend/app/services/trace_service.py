"""Трассировка инсайта (B8+B9): запуск pipeline_runs, его шаги, источники, признаки профиля.

Шаги — pipeline_runs.steps как есть (их пишет генератор, бэкенд не пересчитывает), время
стадии — duration_ms шага. Источники — те же, что у карточки (insight_service), плюс id
документа и название источника из documents. used_profile_fields и prompt_version — из payload
карточки; если генератор их не отдал — null.

USE_MOCKS=true — у моковых инсайтов запусков нет: трасса без шагов, run_id = null.
"""

import re
from typing import Any

from app.core.auth import User, ensure_can_see
from app.core.db import get_session
from app.core.errors import NotFoundError
from app.models import Document
from app.repositories import document_repo, insight_repo, pipeline_run_repo
from app.schemas.trace import InsightTrace
from app.services import insight_service

_DOC_ID = re.compile(r"^N:doc-(\d+)$")


def _sources(payload: dict[str, Any], docs: list[Document]) -> list[dict[str, Any]]:
    by_id = {d.id: d for d in docs}
    out = []
    for src in insight_service.with_document_sources(payload, docs)["sources"]:
        m = _DOC_ID.match(src.get("id") or "")
        doc = by_id.get(int(m.group(1))) if m else None
        out.append(
            {
                **src,
                "document_id": doc.id if doc else None,
                "source_name": doc.source_name if doc else None,
            }
        )
    return out


def _from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "used_profile_fields": payload.get("used_profile_fields") or None,
        "prompt_version": payload.get("prompt_version") or None,
    }


def _mock_trace(insight_id: str) -> InsightTrace:
    from app.services import mock_store

    payload = mock_store.get_insight(insight_id).model_dump(mode="json")
    return InsightTrace(
        insight_id=insight_id,
        company_id=payload["company_id"],
        steps=[],
        sources=payload.get("sources") or [],
        generated_by=payload.get("generated_by"),
        **_from_payload(payload),
    )


def get_trace(insight_id: str, user: User) -> InsightTrace:
    if insight_service.use_mocks():
        trace = _mock_trace(insight_id)
        ensure_can_see(user, trace.company_id)
        return trace
    with get_session() as session:
        row = insight_repo.get(session, insight_id)
        if row is None:
            raise NotFoundError(f"Инсайт {insight_id} не найден")
        ensure_can_see(user, row.company_id)
        payload = dict(row.payload)
        found = document_repo.get_many(session, list(row.document_ids or []))
        docs = [found[i] for i in row.document_ids or [] if i in found]
        run = pipeline_run_repo.get(session, row.run_id) if row.run_id is not None else None
        summary = (run.summary or {}) if run is not None else {}
        duration = None
        if run is not None and run.finished_at is not None:
            duration = int((run.finished_at - run.started_at).total_seconds() * 1000)
        return InsightTrace(
            insight_id=row.id,
            company_id=row.company_id,
            run_id=run.id if run else None,
            generator_run_id=summary.get("run_id"),
            run_status=run.status if run else None,
            role=summary.get("role") or row.role,
            llm_mode=summary.get("llm_mode"),
            started_at=run.started_at if run else None,
            finished_at=run.finished_at if run else None,
            duration_ms=duration,
            steps=list(run.steps or []) if run else [],
            sources=_sources(payload, docs),
            generated_by=row.generated_by,
            **_from_payload(payload),
        )
