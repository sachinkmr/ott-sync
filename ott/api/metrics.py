"""Metrics and analytics API endpoints"""

import logging
from typing import Optional, Literal
from fastapi import HTTPException

from .app import app
from ..db.repositories.metrics import MetricsRepository
from ..db.repositories.errors import ErrorRepository

logger = logging.getLogger("ott-hooks")


def register_metrics_routes():
    """Register metrics and analytics endpoints"""
    
    @app.get(
        "/metrics",
        summary="Get aggregated processing metrics",
        description="Returns aggregated statistics for a time period",
        tags=["Metrics"],
    )
    async def get_metrics(
        service: Optional[Literal["radarr", "sonarr"]] = None,
        hours: int = 24,
    ):
        """Get aggregated processing metrics
        
        Args:
            service: Filter by service ('radarr' or 'sonarr'), None for all
            hours: Time period in hours (default: 24)
            
        Returns:
            Aggregated metrics including checked, cleaned, and error counts
            
        Examples:
        ```bash
        # Get last 24 hours for all services
        curl "http://localhost:9123/metrics"
        
        # Get last 7 days for Radarr only
        curl "http://localhost:9123/metrics?service=radarr&hours=168"
        ```
        
        Example response:
        ```json
        {
          "period_hours": 24,
          "service": "all",
          "total_checked": 150,
          "total_cleaned": 45,
          "total_marked_processed": 105,
          "total_errors": 2
        }
        ```
        """
        try:
            stats = MetricsRepository.get_aggregated_stats(service=service, hours=hours)
            return stats
        except Exception as e:
            logger.error(f"[METRICS] Failed to get metrics: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get(
        "/metrics/hourly",
        summary="Get hourly metrics breakdown",
        description="Returns hourly breakdown of processing metrics",
        tags=["Metrics"],
    )
    async def get_hourly_metrics(
        service: Optional[Literal["radarr", "sonarr"]] = None,
        hours: int = 24,
    ):
        """Get hourly metrics breakdown
        
        Args:
            service: Filter by service ('radarr' or 'sonarr'), None for all
            hours: Number of hours to include (default: 24)
            
        Returns:
            List of hourly statistics
            
        Example:
        ```bash
        curl "http://localhost:9123/metrics/hourly?hours=12"
        ```
        """
        try:
            breakdown = MetricsRepository.get_hourly_breakdown(service=service, hours=hours)
            return breakdown
        except Exception as e:
            logger.error(f"[METRICS] Failed to get hourly breakdown: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get(
        "/errors",
        summary="Get recent error logs",
        description="Returns recent error log entries",
        tags=["Metrics"],
    )
    async def get_errors(
        limit: int = 50,
        unresolved_only: bool = False,
    ):
        """Get recent error logs
        
        Args:
            limit: Maximum number of errors to return (default: 50)
            unresolved_only: Only return unresolved errors (default: False)
            
        Returns:
            List of error log entries
            
        Example:
        ```bash
        # Get last 50 errors
        curl "http://localhost:9123/errors"
        
        # Get unresolved errors only
        curl "http://localhost:9123/errors?unresolved_only=true"
        ```
        """
        try:
            errors = ErrorRepository.get_recent_errors(limit=limit, unresolved_only=unresolved_only)
            return errors
        except Exception as e:
            logger.error(f"[METRICS] Failed to get errors: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
