"""Оценки инсайтов (B7b): одна оценка пользователя на инсайт, повторная обновляет прежнюю.

Статистика (GET /feedback/stats, только admin) — метрики качества: всего оценок, доля useful,
причины у not_useful, разбивка по типу инсайта. Тип хранится в строке оценки, поэтому
статистика не пропадает после refresh, который пересоздаёт инсайты.
"""

from typing import Any

from app.core.auth import User, ensure_admin, ensure_can_see
from app.core.db import get_session
from app.core.errors import NotFoundError
from app.models import FeedbackRow
from app.repositories import feedback_repo, insight_repo
from app.schemas.qa import Feedback, FeedbackRequest, FeedbackStats, TypeStats
from app.services import insight_service

INSIGHT_TYPES = ("risk", "opportunity", "event")
REASONS = ("inaccurate", "irrelevant", "unclear", "other")
NO_REASON = "none"


def _mocks() -> Any:
    from app.services import mock_store

    return mock_store


def _feedback(row: FeedbackRow) -> Feedback:
    return Feedback(
        insight_id=row.insight_id,
        rating=row.rating,  # type: ignore[arg-type]
        comment=row.comment,
        created_at=row.created_at,
        reason=row.reason,  # type: ignore[arg-type]
        updated_at=row.updated_at,
    )


def _insight(session: Any, insight_id: str, user: User) -> Any:
    row = insight_repo.get(session, insight_id)
    if row is None:
        raise NotFoundError(f"Инсайт {insight_id} не найден")
    ensure_can_see(user, row.company_id)
    return row


def save(insight_id: str, data: FeedbackRequest, user: User) -> tuple[Feedback, bool]:
    """Сохранить оценку; второй элемент — True, если это первая оценка пользователя (201)."""
    if insight_service.use_mocks():
        return _mocks().save_feedback(insight_id, data)
    with get_session() as session:
        insight = _insight(session, insight_id, user)
        row, created = feedback_repo.upsert(
            session,
            {
                "insight_id": insight_id,
                "company_id": insight.company_id,
                "insight_type": insight.type,
                "user_id": user.id,
                "rating": data.rating,
                "reason": data.reason,
                "comment": data.comment,
            },
        )
        result = _feedback(row)
        session.commit()
    return result, created


def get_mine(insight_id: str, user: User) -> Feedback | None:
    """Оценка пользователя (для подсветки кнопки) или None, если он ещё не оценивал."""
    if insight_service.use_mocks():
        return _mocks().get_feedback(insight_id)
    with get_session() as session:
        _insight(session, insight_id, user)
        row = feedback_repo.get(session, insight_id, user.id)
        return _feedback(row) if row is not None else None


def _share(useful: int, total: int) -> float | None:
    return round(useful / total, 3) if total else None


def compute_stats(counts: list[dict[str, Any]]) -> FeedbackStats:
    """counts — [{insight_type, rating, reason, n}] -> FeedbackStats (все ключи всегда есть)."""
    total = sum(c["n"] for c in counts)
    useful = sum(c["n"] for c in counts if c["rating"] == "useful")
    by_reason = dict.fromkeys((*REASONS, NO_REASON), 0)
    for c in counts:
        if c["rating"] == "not_useful":
            by_reason[c["reason"] or NO_REASON] += c["n"]
    by_type = {}
    for kind in INSIGHT_TYPES:
        t_total = sum(c["n"] for c in counts if c["insight_type"] == kind)
        t_useful = sum(
            c["n"] for c in counts if c["insight_type"] == kind and c["rating"] == "useful"
        )
        by_type[kind] = TypeStats(total=t_total, useful_share=_share(t_useful, t_total))
    return FeedbackStats(
        total=total,
        useful=useful,
        not_useful=total - useful,
        useful_share=_share(useful, total),
        by_reason=by_reason,
        by_type=by_type,
    )


def stats(user: User) -> FeedbackStats:
    ensure_admin(user)
    if insight_service.use_mocks():
        return compute_stats(_mocks().feedback_counts())
    with get_session() as session:
        return compute_stats(feedback_repo.counts(session))
