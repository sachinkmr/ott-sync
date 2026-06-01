"""Repository for dead-media notification persistence.

Backs the dead-media manager's dedup + action-tracking state. Phase A used
an in-memory dict that was reset on every container restart; this version
survives restarts and is the source of truth for whether a (series,
detector) bucket has been notified within the dedup TTL.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import update as sa_update
from sqlalchemy.exc import SQLAlchemyError

from ..client import get_db
from ..schema import DeadMediaNotificationModel

logger = logging.getLogger("ott-hooks")


class DeadMediaRepository:
    """Storage + idempotent updates for dead-media notifications."""

    # ---- write ----

    @staticmethod
    def create_notification(
        group_key: str,
        arr: str,
        item_id: int,
        detector: str,
        title: Optional[str],
        affected_hashes: list[str],
        affected_episodes: list[str],
        affected_episode_ids: list[int],
        telegram_chat_id: Optional[str] = None,
    ) -> Optional[int]:
        """Insert a new notification row. Returns its id, or None on failure.

        Called *before* the Telegram send so we have a callback_data id ready.
        After the send succeeds, set telegram_message_id via update_message_id.
        """
        db = get_db()
        try:
            with db.session() as session:
                row = DeadMediaNotificationModel(
                    group_key=group_key,
                    arr=arr,
                    item_id=item_id,
                    detector=detector,
                    title=title,
                    sent_at=datetime.utcnow(),
                    affected_hashes=json.dumps(affected_hashes) if affected_hashes else None,
                    affected_episodes=json.dumps(affected_episodes) if affected_episodes else None,
                    affected_episode_ids=json.dumps(affected_episode_ids) if affected_episode_ids else None,
                    telegram_chat_id=telegram_chat_id,
                )
                session.add(row)
                session.flush()  # populate row.id without releasing the session
                new_id = row.id
                session.commit()
                return new_id
        except SQLAlchemyError as e:
            logger.error(f"[dead-media:repo] create_notification failed: {e}")
            return None

    @staticmethod
    def update_message_id(notification_id: int, message_id: int) -> bool:
        """Stamp the Telegram message_id onto a notification row.

        Called right after Telegram returns ok so callback handlers can
        editMessageText later (e.g. replace pending buttons with "✓ Search
        triggered" once the user acts).
        """
        db = get_db()
        try:
            with db.session() as session:
                row = session.get(DeadMediaNotificationModel, notification_id)
                if not row:
                    return False
                row.telegram_message_id = message_id
                session.commit()
                return True
        except SQLAlchemyError as e:
            logger.error(f"[dead-media:repo] update_message_id failed: {e}")
            return False

    @staticmethod
    def claim_action(notification_id: int, action: str) -> bool:
        """Atomically record an action on a notification.

        Returns True if this call won the race (the row had action_taken =
        NULL and we set it). Returns False if someone else already acted, or
        the row doesn't exist. The caller should only proceed with the
        actual *arr operations when this returns True.

        Uses a conditional UPDATE rather than read-then-write so two
        concurrent button-presses (or callback + cron-time auto action)
        don't both fire side-effects.
        """
        db = get_db()
        try:
            with db.session() as session:
                stmt = (
                    sa_update(DeadMediaNotificationModel)
                    .where(DeadMediaNotificationModel.id == notification_id)
                    .where(DeadMediaNotificationModel.action_taken.is_(None))
                    .values(action_taken=action, action_at=datetime.utcnow())
                )
                result = session.execute(stmt)
                won = result.rowcount == 1
                session.commit()
                return won
        except SQLAlchemyError as e:
            logger.error(f"[dead-media:repo] claim_action failed: {e}")
            return False

    # ---- read ----

    @staticmethod
    def get(notification_id: int) -> Optional[dict[str, Any]]:
        """Fetch a single notification by id. Returns dict snapshot or None."""
        db = get_db()
        try:
            with db.session() as session:
                row = session.get(DeadMediaNotificationModel, notification_id)
                if not row:
                    return None
                return DeadMediaRepository._to_dict(row)
        except SQLAlchemyError as e:
            logger.error(f"[dead-media:repo] get failed: {e}")
            return None

    @staticmethod
    def get_recent_by_group_key(
        group_key: str,
        ttl_seconds: int,
    ) -> Optional[dict[str, Any]]:
        """Return the most recent notification for this group_key if it was
        sent within the last `ttl_seconds`. Used by the cron to dedup.

        Returns None if no notification or it's too old (cron can re-send).
        """
        db = get_db()
        cutoff = datetime.utcnow() - timedelta(seconds=ttl_seconds)
        try:
            with db.session() as session:
                row = (
                    session.query(DeadMediaNotificationModel)
                    .filter(DeadMediaNotificationModel.group_key == group_key)
                    .filter(DeadMediaNotificationModel.sent_at >= cutoff)
                    .order_by(DeadMediaNotificationModel.sent_at.desc())
                    .first()
                )
                if not row:
                    return None
                return DeadMediaRepository._to_dict(row)
        except SQLAlchemyError as e:
            logger.error(f"[dead-media:repo] get_recent_by_group_key failed: {e}")
            return None

    @staticmethod
    def cleanup_old(retention_days: int = 30) -> int:
        """Delete notifications older than `retention_days`. Returns count.

        Notifications are point-in-time records; once an action is taken or
        the alert ages out, there's no value in keeping them long-term. We
        keep recent ones for dedup and debugging.
        """
        db = get_db()
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        try:
            with db.session() as session:
                deleted = (
                    session.query(DeadMediaNotificationModel)
                    .filter(DeadMediaNotificationModel.sent_at < cutoff)
                    .delete()
                )
                session.commit()
                return deleted or 0
        except SQLAlchemyError as e:
            logger.error(f"[dead-media:repo] cleanup_old failed: {e}")
            return 0

    # ---- helpers ----

    @staticmethod
    def _to_dict(row: DeadMediaNotificationModel) -> dict[str, Any]:
        """Detach a SQLAlchemy row to a plain dict so callers don't have to
        worry about session lifetime."""
        return {
            "id": row.id,
            "group_key": row.group_key,
            "arr": row.arr,
            "item_id": row.item_id,
            "detector": row.detector,
            "title": row.title,
            "sent_at": row.sent_at,
            "telegram_message_id": row.telegram_message_id,
            "telegram_chat_id": row.telegram_chat_id,
            "action_taken": row.action_taken,
            "action_at": row.action_at,
            "affected_hashes": json.loads(row.affected_hashes) if row.affected_hashes else [],
            "affected_episodes": json.loads(row.affected_episodes) if row.affected_episodes else [],
            "affected_episode_ids": json.loads(row.affected_episode_ids) if row.affected_episode_ids else [],
        }
