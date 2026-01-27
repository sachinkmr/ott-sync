"""Repository for tag history operations"""

import logging
from datetime import datetime
from typing import Optional

from ..client import get_db
from ..schema import TagHistoryModel

logger = logging.getLogger("ott-hooks")


class TagRepository:
    """Repository for storing and querying tag history"""
    
    @staticmethod
    def log_tag_change(
        item_id: int,
        service: str,
        item_type: str,
        title: str,
        tag_name: str,
        action: str,
        triggered_by: Optional[str] = None,
    ) -> bool:
        """Log tag change
        
        Args:
            item_id: Item ID from Radarr/Sonarr
            service: 'radarr' or 'sonarr'
            item_type: 'movie' or 'series'
            title: Item title
            tag_name: Tag name
            action: 'added' or 'removed'
            triggered_by: What triggered this change (webhook, cron, manual, override)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            db = get_db()
            
            with db.session() as session:
                tag_entry = TagHistoryModel(
                    timestamp=datetime.utcnow(),
                    item_id=item_id,
                    service=service,
                    item_type=item_type,
                    title=title,
                    tag_name=tag_name,
                    action=action,
                    triggered_by=triggered_by,
                )
                
                session.add(tag_entry)
                session.commit()
                
                logger.debug(f"[TAG] Logged: {tag_name} {action} for {title}")
                return True
                
        except Exception as e:
            logger.error(f"[TAG] Failed to log tag change: {e}", exc_info=True)
            return False
    
    @staticmethod
    def get_item_tag_history(item_id: int, service: str) -> list[dict]:
        """Get tag history for a specific item
        
        Args:
            item_id: Item ID
            service: 'radarr' or 'sonarr'
            
        Returns:
            List of tag history entries
        """
        try:
            db = get_db()
            
            with db.session() as session:
                entries = session.query(TagHistoryModel).filter(
                    TagHistoryModel.item_id == item_id,
                    TagHistoryModel.service == service,
                ).order_by(TagHistoryModel.timestamp.desc()).all()
                
                return [
                    {
                        'timestamp': e.timestamp.isoformat(),
                        'tag_name': e.tag_name,
                        'action': e.action,
                        'triggered_by': e.triggered_by,
                    }
                    for e in entries
                ]
                
        except Exception as e:
            logger.error(f"[TAG] Failed to get tag history: {e}", exc_info=True)
            return []
