"""Repository for processing metrics operations"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..client import get_db
from ..schema import ProcessingMetricsModel
from ...models import ProcessingMetrics

logger = logging.getLogger("ott-hooks")


class MetricsRepository:
    """Repository for storing and querying processing metrics"""
    
    @staticmethod
    def save_metrics(
        service: str,
        operation_type: str,
        metrics: ProcessingMetrics,
        processing_time_ms: Optional[int] = None,
    ) -> bool:
        """Save processing metrics to database
        
        Args:
            service: 'radarr' or 'sonarr'
            operation_type: 'webhook', 'cron', 'manual'
            metrics: ProcessingMetrics instance
            processing_time_ms: Processing time in milliseconds
            
        Returns:
            True if successful, False otherwise
        """
        try:
            db = get_db()
            
            with db.session() as session:
                items_processed = (
                    metrics.checked +
                    metrics.cleaned +
                    metrics.marked_processed +
                    metrics.skipped_override +
                    metrics.already_processed
                )
                
                metrics_model = ProcessingMetricsModel(
                    timestamp=datetime.utcnow(),
                    service=service,
                    operation_type=operation_type,
                    checked=metrics.checked,
                    cleaned=metrics.cleaned,
                    marked_processed=metrics.marked_processed,
                    skipped_override=metrics.skipped_override,
                    already_processed=metrics.already_processed,
                    errors=metrics.errors,
                    processing_time_ms=processing_time_ms,
                    items_processed=items_processed,
                )
                
                session.add(metrics_model)
                session.commit()
                
                logger.debug(f"[METRICS] Saved: {service}/{operation_type} - {items_processed} items")
                return True
                
        except Exception as e:
            logger.error(f"[METRICS] Failed to save metrics: {e}", exc_info=True)
            return False
    
    @staticmethod
    def get_aggregated_stats(
        service: Optional[str] = None,
        hours: int = 24,
    ) -> dict:
        """Get aggregated statistics for a time period
        
        Args:
            service: Filter by service ('radarr' or 'sonarr'), None for all
            hours: Time period in hours (default: 24)
            
        Returns:
            Dictionary with aggregated statistics
        """
        try:
            db = get_db()
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            with db.session() as session:
                query = session.query(
                    func.sum(ProcessingMetricsModel.checked).label('total_checked'),
                    func.sum(ProcessingMetricsModel.cleaned).label('total_cleaned'),
                    func.sum(ProcessingMetricsModel.marked_processed).label('total_marked'),
                    func.sum(ProcessingMetricsModel.skipped_override).label('total_skipped'),
                    func.sum(ProcessingMetricsModel.already_processed).label('total_already'),
                    func.sum(ProcessingMetricsModel.errors).label('total_errors'),
                    func.sum(ProcessingMetricsModel.items_processed).label('total_items'),
                    func.count(ProcessingMetricsModel.id).label('total_operations'),
                ).filter(
                    ProcessingMetricsModel.timestamp >= cutoff_time
                )
                
                if service:
                    query = query.filter(ProcessingMetricsModel.service == service)
                
                result = query.first()
                
                return {
                    'period_hours': hours,
                    'service': service or 'all',
                    'total_checked': result.total_checked or 0,
                    'total_cleaned': result.total_cleaned or 0,
                    'total_marked_processed': result.total_marked or 0,
                    'total_skipped_override': result.total_skipped or 0,
                    'total_already_processed': result.total_already or 0,
                    'total_errors': result.total_errors or 0,
                    'total_items_processed': result.total_items or 0,
                    'total_operations': result.total_operations or 0,
                }
                
        except Exception as e:
            logger.error(f"[METRICS] Failed to get stats: {e}", exc_info=True)
            return {}
    
    @staticmethod
    def get_hourly_breakdown(service: Optional[str] = None, hours: int = 24) -> list[dict]:
        """Get hourly breakdown of metrics
        
        Args:
            service: Filter by service ('radarr' or 'sonarr'), None for all
            hours: Number of hours to include (default: 24)
            
        Returns:
            List of hourly statistics
        """
        try:
            db = get_db()
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            with db.session() as session:
                # SQLite date formatting for hourly grouping
                query = session.query(
                    func.strftime('%Y-%m-%d %H:00:00', ProcessingMetricsModel.timestamp).label('hour'),
                    func.sum(ProcessingMetricsModel.items_processed).label('items'),
                    func.sum(ProcessingMetricsModel.errors).label('errors'),
                    func.count(ProcessingMetricsModel.id).label('operations'),
                ).filter(
                    ProcessingMetricsModel.timestamp >= cutoff_time
                ).group_by('hour').order_by('hour')
                
                if service:
                    query = query.filter(ProcessingMetricsModel.service == service)
                
                results = query.all()
                
                return [
                    {
                        'hour': r.hour,
                        'items_processed': r.items or 0,
                        'errors': r.errors or 0,
                        'operations': r.operations or 0,
                    }
                    for r in results
                ]
                
        except Exception as e:
            logger.error(f"[METRICS] Failed to get hourly breakdown: {e}", exc_info=True)
            return []
