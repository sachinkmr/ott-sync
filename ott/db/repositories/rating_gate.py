"""Repository for rating-gate pending-evaluation and pending-approval tables."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from ..client import get_db
from ..schema import PendingEvaluationModel, PendingApprovalModel

logger = logging.getLogger("ott-hooks")


class PendingEvaluationRepository:
    """CRUD for deferred items that had no trustworthy rating at decision time."""

    @staticmethod
    def create(
        media_type: str,
        tmdb_id: int,
        arr_type: str,
        arr_item_id: int,
        title: str,
        defer_days: int = 7,
        reason: str = "",
    ) -> bool:
        try:
            db = get_db()
            with db.session() as session:
                entry = PendingEvaluationModel(
                    media_type=media_type,
                    tmdb_id=tmdb_id,
                    arr_type=arr_type,
                    arr_item_id=arr_item_id,
                    title=title,
                    first_seen_at=datetime.utcnow(),
                    next_check_at=datetime.utcnow() + timedelta(days=defer_days),
                    attempts=0,
                    last_reason=reason,
                )
                session.add(entry)
                session.commit()
            return True
        except Exception as e:
            logger.error(f"[RATING-GATE] Failed to create pending_evaluation: {e}", exc_info=True)
            return False

    @staticmethod
    def get_due(limit: int = 100) -> list[dict]:
        """Return rows where next_check_at <= now."""
        try:
            db = get_db()
            with db.session() as session:
                rows = (
                    session.query(PendingEvaluationModel)
                    .filter(PendingEvaluationModel.next_check_at <= datetime.utcnow())
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "id": r.id, "media_type": r.media_type, "tmdb_id": r.tmdb_id,
                        "arr_type": r.arr_type, "arr_item_id": r.arr_item_id,
                        "title": r.title, "attempts": r.attempts,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"[RATING-GATE] Failed to get due evaluations: {e}", exc_info=True)
            return []

    @staticmethod
    def bump(row_id: int, defer_days: int = 7) -> bool:
        """Increment attempts and push next_check_at out."""
        try:
            db = get_db()
            with db.session() as session:
                row = session.query(PendingEvaluationModel).get(row_id)
                if not row:
                    return False
                row.attempts += 1
                row.next_check_at = datetime.utcnow() + timedelta(days=defer_days)
                session.commit()
            return True
        except Exception as e:
            logger.error(f"[RATING-GATE] Failed to bump evaluation {row_id}: {e}", exc_info=True)
            return False

    @staticmethod
    def delete(row_id: int) -> bool:
        try:
            db = get_db()
            with db.session() as session:
                row = session.query(PendingEvaluationModel).get(row_id)
                if row:
                    session.delete(row)
                    session.commit()
            return True
        except Exception as e:
            logger.error(f"[RATING-GATE] Failed to delete evaluation {row_id}: {e}", exc_info=True)
            return False

    @staticmethod
    def find_by_item(arr_type: str, arr_item_id: int) -> Optional[dict]:
        try:
            db = get_db()
            with db.session() as session:
                row = (
                    session.query(PendingEvaluationModel)
                    .filter(
                        PendingEvaluationModel.arr_type == arr_type,
                        PendingEvaluationModel.arr_item_id == arr_item_id,
                    )
                    .first()
                )
                if not row:
                    return None
                return {
                    "id": row.id, "media_type": row.media_type, "tmdb_id": row.tmdb_id,
                    "arr_type": row.arr_type, "arr_item_id": row.arr_item_id,
                    "title": row.title, "attempts": row.attempts,
                }
        except Exception as e:
            logger.error(f"[RATING-GATE] Failed to find evaluation: {e}", exc_info=True)
            return None


class PendingApprovalRepository:
    """CRUD for items awaiting user approval via Telegram."""

    @staticmethod
    def create(
        media_type: str,
        tmdb_id: int,
        arr_type: str,
        arr_item_id: int,
        title: str,
        reason: str,
        rating_score_pct: Optional[float] = None,
        trending: Optional[bool] = None,
    ) -> bool:
        try:
            db = get_db()
            with db.session() as session:
                entry = PendingApprovalModel(
                    media_type=media_type,
                    tmdb_id=tmdb_id,
                    arr_type=arr_type,
                    arr_item_id=arr_item_id,
                    title=title,
                    created_at=datetime.utcnow(),
                    reason=reason,
                    rating_score_pct=rating_score_pct,
                    trending=trending,
                )
                session.add(entry)
                session.commit()
            return True
        except Exception as e:
            logger.error(f"[RATING-GATE] Failed to create pending_approval: {e}", exc_info=True)
            return False

    @staticmethod
    def resolve(arr_type: str, arr_item_id: int, resolution: str) -> bool:
        """Mark an approval as resolved (download or skip)."""
        try:
            db = get_db()
            with db.session() as session:
                row = (
                    session.query(PendingApprovalModel)
                    .filter(
                        PendingApprovalModel.arr_type == arr_type,
                        PendingApprovalModel.arr_item_id == arr_item_id,
                        PendingApprovalModel.resolved_at.is_(None),
                    )
                    .first()
                )
                if not row:
                    return False
                row.resolved_at = datetime.utcnow()
                row.resolution = resolution
                session.commit()
            return True
        except Exception as e:
            logger.error(f"[RATING-GATE] Failed to resolve approval: {e}", exc_info=True)
            return False

    @staticmethod
    def is_pending(arr_type: str, arr_item_id: int) -> bool:
        """Check if there's an unresolved approval for this item."""
        try:
            db = get_db()
            with db.session() as session:
                return (
                    session.query(PendingApprovalModel)
                    .filter(
                        PendingApprovalModel.arr_type == arr_type,
                        PendingApprovalModel.arr_item_id == arr_item_id,
                        PendingApprovalModel.resolved_at.is_(None),
                    )
                    .first()
                ) is not None
        except Exception as e:
            logger.error(f"[RATING-GATE] Failed to check pending approval: {e}", exc_info=True)
            return False
