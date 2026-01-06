"""Health and utility endpoints"""

import logging
from typing import Callable

from .app import app

logger = logging.getLogger("ott-hooks")


def register_health_routes(get_radarr_mgr: Callable, get_sonarr_mgr: Callable):
    """Register health check and utility endpoints
    
    Args:
        get_radarr_mgr: Callable that returns current RadarrManager instance
        get_sonarr_mgr: Callable that returns current SonarrManager instance
    """
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint
        
        Returns:
            Status OK
        """
        return {"status": "ok"}
    
    @app.get("/cron")
    async def trigger_cron():
        """Manually trigger cron cleanup
        
        Runs cleanup for both Radarr and Sonarr.
        Useful for testing or manual reconciliation.
        
        Returns:
            Metrics from both services
        """
        logger.info("[API] Manual cron trigger requested")
        
        radarr_metrics = get_radarr_mgr().cron_cleanup()
        sonarr_metrics = get_sonarr_mgr().cron_cleanup()
        
        return {
            "ok": True,
            "radarr": {
                "checked": radarr_metrics.checked,
                "cleaned": radarr_metrics.cleaned,
                "marked_processed": radarr_metrics.marked_processed,
                "skipped_override": radarr_metrics.skipped_override,
                "already_processed": radarr_metrics.already_processed,
                "errors": radarr_metrics.errors,
            },
            "sonarr": {
                "checked": sonarr_metrics.checked,
                "cleaned": sonarr_metrics.cleaned,
                "marked_processed": sonarr_metrics.marked_processed,
                "skipped_override": sonarr_metrics.skipped_override,
                "already_processed": sonarr_metrics.already_processed,
                "errors": sonarr_metrics.errors,
            }
        }
