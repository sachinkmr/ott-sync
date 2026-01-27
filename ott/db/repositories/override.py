"""Repository for override history operations"""

import json
import logging
from datetime import datetime
from typing import Optional

from ..client import get_db
from ..schema import OverrideHistoryModel

logger = logging.getLogger("ott-hooks")


class OverrideRepository:
    """Repository for storing and querying override history"""
    
    @staticmethod
    def log_override(
        item_id: int,
        service: str,
        item_type: str,
        title: str,
        year: Optional[int],
        user_action: str,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        reason: Optional[str] = None,
        previous_state: Optional[dict] = None,
        new_state: Optional[dict] = None,
        blocked_providers: Optional[list[str]] = None,
    ) -> bool:
        """Log user override action
        
        Args:
            item_id: Item ID from Radarr/Sonarr
            service: 'radarr' or 'sonarr'
            item_type: 'movie' or 'series'
            title: Item title
            year: Release year
            user_action: Action type (e.g., 'override_download', 'undo_override')
            user_id: Telegram user ID
            user_name: Telegram username
            reason: Reason for override
            previous_state: Previous item state (tags, monitored, etc.)
            new_state: New item state
            blocked_providers: List of providers that were blocking this item
            
        Returns:
            True if successful, False otherwise
        """
        try:
            db = get_db()
            
            with db.session() as session:
                override_entry = OverrideHistoryModel(
                    timestamp=datetime.utcnow(),
                    item_id=item_id,
                    service=service,
                    item_type=item_type,
                    title=title,
                    year=year,
                    user_action=user_action,
                    user_id=user_id,
                    user_name=user_name,
                    reason=reason,
                    previous_state=json.dumps(previous_state) if previous_state else None,
                    new_state=json.dumps(new_state) if new_state else None,
                    blocked_providers=json.dumps(blocked_providers) if blocked_providers else None,
                )
                
                session.add(override_entry)
                session.commit()
                
                logger.info(f"[OVERRIDE] Logged: {title} ({year}) - {user_action}")
                return True
                
        except Exception as e:
            logger.error(f"[OVERRIDE] Failed to log override: {e}", exc_info=True)
            return False
    
    @staticmethod
    def get_item_history(item_id: int, service: str) -> list[dict]:
        """Get override history for a specific item
        
        Args:
            item_id: Item ID
            service: 'radarr' or 'sonarr'
            
        Returns:
            List of override history entries
        """
        try:
            db = get_db()
            
            with db.session() as session:
                entries = session.query(OverrideHistoryModel).filter(
                    OverrideHistoryModel.item_id == item_id,
                    OverrideHistoryModel.service == service,
                ).order_by(OverrideHistoryModel.timestamp.desc()).all()
                
                return [
                    {
                        'timestamp': e.timestamp.isoformat(),
                        'title': e.title,
                        'year': e.year,
                        'user_action': e.user_action,
                        'user_name': e.user_name,
                        'reason': e.reason,
                        'blocked_providers': json.loads(e.blocked_providers) if e.blocked_providers else None,
                    }
                    for e in entries
                ]
                
        except Exception as e:
            logger.error(f"[OVERRIDE] Failed to get item history: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_user_overrides(user_id: str, limit: int = 50) -> list[dict]:
        """Get override history for a specific user
        
        Args:
            user_id: Telegram user ID
            limit: Maximum number of entries to return
            
        Returns:
            List of override history entries
        """
        try:
            db = get_db()
            
            with db.session() as session:
                entries = session.query(OverrideHistoryModel).filter(
                    OverrideHistoryModel.user_id == user_id,
                ).order_by(OverrideHistoryModel.timestamp.desc()).limit(limit).all()
                
                return [
                    {
                        'timestamp': e.timestamp.isoformat(),
                        'item_id': e.item_id,
                        'service': e.service,
                        'title': e.title,
                        'year': e.year,
                        'user_action': e.user_action,
                        'blocked_providers': json.loads(e.blocked_providers) if e.blocked_providers else None,
                    }
                    for e in entries
                ]
                
        except Exception as e:
            logger.error(f"[OVERRIDE] Failed to get user overrides: {e}", exc_info=True)
            return []
