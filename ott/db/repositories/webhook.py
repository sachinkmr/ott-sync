"""Repository for webhook log operations"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from ..client import get_db
from ..schema import WebhookLogModel

logger = logging.getLogger("ott-hooks")


class WebhookRepository:
    """Repository for storing and querying webhook logs"""
    
    @staticmethod
    def log_webhook(
        service: str,
        event_type: str,
        item_id: int,
        title: str,
        payload: dict,
        status: str,
        processing_time_ms: Optional[int] = None,
        action_taken: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Log webhook event
        
        Args:
            service: 'radarr' or 'sonarr'
            event_type: Event type (MovieAdded, SeriesAdded, etc.)
            item_id: Item ID
            title: Item title
            payload: Full webhook payload
            status: 'success', 'error', or 'skipped'
            processing_time_ms: Processing time in milliseconds
            action_taken: Action taken (blocked, allowed, error)
            error_message: Error message if status is error
            
        Returns:
            True if successful, False otherwise
        """
        try:
            db = get_db()
            
            # Generate payload hash for deduplication
            payload_str = json.dumps(payload, sort_keys=True)
            payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
            
            with db.session() as session:
                webhook_entry = WebhookLogModel(
                    timestamp=datetime.utcnow(),
                    service=service,
                    event_type=event_type,
                    item_id=item_id,
                    title=title,
                    payload_hash=payload_hash,
                    status=status,
                    processing_time_ms=processing_time_ms,
                    action_taken=action_taken,
                    error_message=error_message,
                )
                
                session.add(webhook_entry)
                session.commit()
                
                logger.debug(f"[WEBHOOK] Logged: {event_type} - {title} ({status})")
                return True
                
        except Exception as e:
            logger.error(f"[WEBHOOK] Failed to log webhook: {e}", exc_info=True)
            return False
    
    @staticmethod
    def check_duplicate(payload: dict, window_seconds: int = 60) -> bool:
        """Check if webhook is a duplicate within time window
        
        Args:
            payload: Webhook payload
            window_seconds: Time window in seconds to check for duplicates
            
        Returns:
            True if duplicate found, False otherwise
        """
        try:
            db = get_db()
            
            # Generate payload hash
            payload_str = json.dumps(payload, sort_keys=True)
            payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
            
            cutoff_time = datetime.utcnow() - timedelta(seconds=window_seconds)
            
            with db.session() as session:
                duplicate = session.query(WebhookLogModel).filter(
                    WebhookLogModel.payload_hash == payload_hash,
                    WebhookLogModel.timestamp >= cutoff_time,
                ).first()
                
                return duplicate is not None
                
        except Exception as e:
            logger.error(f"[WEBHOOK] Failed to check duplicate: {e}", exc_info=True)
            return False
