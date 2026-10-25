"""Вопросы по инсайту (B7b): постановка ask, ответ в worker, история.

POST /insights/{id}/ask -> ask(): в одной транзакции вопрос (insight_questions, pending) и задача
jobs(kind=ask) — API сразу отвечает 202 {job_id, question_id}. Worker берёт задачу как refresh
(job_service.execute) и вызывает run_question(): ответ из qa_port, статус вопроса повторяет
задачу. Ответ виден в GET /jobs/{id}/result и в GET /insights/{id}/questions.

USE_MOCKS=true — как до B7: вопрос и ответ в памяти (mock_store), ответ в фоне FastAPI.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from app.core.auth import User, ensure_can_see
from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError
from app.ml_ports import qa_port
from app.repositories import insight_repo, job_repo, question_repo
from app.schemas.common import Page
from app.schemas.insight import Source
from app.schemas.qa import QUESTIONS_PER_INSIGHT, AskAccepted, Question
from app.services import insight_service

logger = logging.getLogger(__name__)

KIND_ASK = "ask"
ERROR_MAX_LEN = 2000


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _insight_company(session: Any, insight_id: str) -> str:
    row = insight_repo.get(session, insight_id)
    if row is None:
        raise NotFoundError(f"Инсайт {insight_id} не найден")
    return row.company_id


def ask(
    insight_id: str, question: str, user: User
) -> tuple[AskAccepted, Callable[[], None] | None]:
    """Поставить вопрос. Второй элемент — работа для фона FastAPI (только у моков)."""
    if insight_service.use_mocks():
        from app.services import mock_store

        job_id, question_id = mock_store.start_ask(insight_id, question)
        return AskAccepted(job_id=job_id, question_id=question_id), (
            lambda: mock_store.run_ask(job_id)
        )

    with get_session() as session:
        company_id = _insight_company(session, insight_id)
        ensure_can_see(user, company_id)
        question_repo.lock_insight(session, insight_id)
        if question_repo.count_for_insight(session, insight_id) >= QUESTIONS_PER_INSIGHT:
            raise ConflictError(
                f"Лимит {QUESTIONS_PER_INSIGHT} вопросов к инсайту {insight_id} исчерпан"
            )
        job_id, question_id = _new_id("job"), _new_id("q")
        job_repo.insert_pending(session, job_id, company_id, KIND_ASK, {"question_id": question_id})
        question_repo.add(
            session,
            {
                "id": question_id,
                "insight_id": insight_id,
                "company_id": company_id,
                "user_id": user.id,
                "question": question,
                "status": "pending",
                "job_id": job_id,
            },
        )
        session.commit()
    logger.info("ask %s: вопрос %s, задача %s", insight_id, question_id, job_id)
    return AskAccepted(job_id=job_id, question_id=question_id), None


def _question(row: Any) -> Question:
    return Question(
        id=row.id,
        insight_id=row.insight_id,
        question=row.question,
        answer=row.answer,
        sources=row.sources or [],
        status=row.status,
        job_id=row.job_id,
        created_at=row.created_at,
    )


def list_questions(insight_id: str, user: User, limit: int, offset: int) -> Page[Question]:
    """История вопросов пользователя по инсайту, старые первыми."""
    if insight_service.use_mocks():
        from app.services import mock_store

        items, total = mock_store.list_questions(insight_id, limit, offset)
        return Page[Question](items=items, total=total, limit=limit, offset=offset)
    with get_session() as session:
        ensure_can_see(user, _insight_company(session, insight_id))
        rows, total = question_repo.list_for_user(session, insight_id, user.id, limit, offset)
        items = [_question(r) for r in rows]
    return Page[Question](items=items, total=total, limit=limit, offset=offset)


# ---------- для worker ----------


def _finish_failed(question_id: str, error: str) -> tuple[str, str]:
    with get_session() as session:
        question_repo.set_status(session, question_id, "failed")
        session.commit()
    return "failed", error[:ERROR_MAX_LEN]


def run_question(params: dict[str, Any]) -> tuple[str, str | None]:
    """Ответить на вопрос задачи ask; вернуть (статус задачи, ошибка)."""
    question_id = params.get("question_id", "")
    with get_session() as session:
        q = question_repo.get(session, question_id)
        if q is None:
            return "failed", f"Вопрос {question_id} не найден"
        question_repo.set_status(session, question_id, "running")
        history = [
            {"question": h.question, "answer": h.answer}
            for h in question_repo.answered_before(session, q)
        ]
        insight_id, text = q.insight_id, q.question
        session.commit()
    try:
        insight = insight_service.db_insight_payload(insight_id)
    except NotFoundError:
        return _finish_failed(
            question_id, f"Инсайта {insight_id} больше нет (инсайты компании обновил refresh)"
        )
    try:
        result = qa_port.answer_question(insight, text, history)
        sources = [Source.model_validate(s).model_dump(mode="json") for s in result["sources"]]
    except Exception as exc:  # noqa: BLE001 — любой сбой превращаем в статус вопроса и задачи
        logger.exception("вопрос %s: ответ не получен", question_id)
        return _finish_failed(question_id, f"{type(exc).__name__}: {exc}")
    with get_session() as session:
        question_repo.set_answer(session, question_id, result["answer"], sources)
        session.commit()
    return "done", None


def result_for_job(session: Any, job_id: str) -> dict[str, Any] | None:
    """Ответ задачи ask: {answer, sources} или None, если вопроса нет."""
    q = question_repo.by_job(session, job_id)
    if q is None:
        return None
    return {"answer": q.answer or "", "sources": q.sources or []}
