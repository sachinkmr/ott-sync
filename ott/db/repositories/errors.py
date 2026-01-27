"""Repository for error log operations"""

import json
import logging
import traceback
from datetime import datetime
from typing import Optional

from ..client import get_db
from ..schema import ErrorLogModel

logger = logging.getLogger("ott-hooks")


class ErrorRepository:
    """Repository for storing and querying error logs"""
    
    @staticmethod
    def log_error(
        error_type: str,
        error_message: str,
        severity: str = "error",
        service: Optional[str] = None,
        item_id: Optional[int] = None,
        endpoint: Optional[str] = None,
        operation: Optional[str] = None,
        stack_trace: Optional[str] = None,
        context_data: Optional[dict] = None,
    ) -> bool:
        """Log error to database
        
        Args:
            error_type: Exception class name
            error_message: Error message
            severity: 'error', 'warning', or 'critical'
            service: 'radarr', 'sonarr', or None
            item_id: Related item ID
            endpoint: API endpoint that failed
            operation: Operation being performed
            stack_trace: Full stack trace
            context_data: Additional context as dictionary
            
        Returns:
            True if successful, False otherwise
        """
        try:
            db = get_db()
            
            with db.session() as session:
                error_entry = ErrorLogModel(
                    timestamp=datetime.utcnow(),
                    error_type=error_type,
                    severity=severity,
                    service=service,
                    item_id=item_id,
                    endpoint=endpoint,
                    operation=operation,
                    error_message=error_message,
                    stack_trace=stack_trace or traceback.format_exc(),
                    context_data=json.dumps(context_data) if context_data else None,
                    resolved=False,
                )
                
                session.add(error_entry)
                session.commit()
                
                logger.debug(f"[ERROR] Logged: {error_type} - {error_message[:100]}")
                return True
                
        except Exception as e:
            # Don't let error logging itself crash the application
            logger.error(f"[ERROR] Failed to log error: {e}", exc_info=True)
            return False
    
    @staticmethod
    def mark_resolved(error_id: int) -> bool:
        """Mark error as resolved
        
        Args:
            error_id: Error log ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            db = get_db()
            
            with db.session() as session:
                error = session.query(ErrorLogModel).filter(
                    ErrorLogModel.id == error_id
                ).first()
                
                if error:
                    error.resolved = True
                    error.resolved_at = datetime.utcnow()
                    session.commit()
                    return True
                
                return False
                
        except Exception as e:
            logger.error(f"[ERROR] Failed to mark error as resolved: {e}", exc_info=True)
            return False
    
    @staticmethod
    def get_recent_errors(limit: int = 50, unresolved_only: bool = False) -> list[dict]:
        """Get recent errors
        
        Args:
            limit: Maximum number of errors to return
            unresolved_only: Only return unresolved errors
            
        Returns:
            List of error log entries
        """
        try:
            db = get_db()
            
            with db.session() as session:
                query = session.query(ErrorLogModel)
                
                if unresolved_only:
                    query = query.filter(ErrorLogModel.resolved == False)
                
                errors = query.order_by(ErrorLogModel.timestamp.desc()).limit(limit).all()
                
                return [
                    {
                        'id': e.id,
                        'timestamp': e.timestamp.isoformat(),
                        'error_type': e.error_type,
                        'severity': e.severity,
                        'service': e.service,
                        'item_id': e.item_id,
                        'error_message': e.error_message,
                        'operation': e.operation,
                        'resolved': e.resolved,
                    }
                    for e in errors
                ]
                
        except Exception as e:
            logger.error(f"[ERROR] Failed to get recent errors: {e}", exc_info=True)
            return []
