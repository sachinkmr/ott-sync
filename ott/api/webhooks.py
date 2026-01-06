"""Webhook endpoints for Radarr/Sonarr"""

import logging
from typing import Callable
from fastapi import Request

from .app import app

logger = logging.getLogger("ott-hooks")


def register_webhook_routes(get_radarr_mgr: Callable, get_sonarr_mgr: Callable):
    """Register webhook endpoints for Radarr and Sonarr
    
    Args:
        get_radarr_mgr: Callable that returns current RadarrManager instance
        get_sonarr_mgr: Callable that returns current SonarrManager instance
    """
    
    @app.post("/radarr")
    async def radarr_webhook(request: Request):
        """Handle webhook from Radarr
        
        Processes events: MovieAdded, Grab
        """
        payload = await request.json()
        logger.debug(f"[WEBHOOK] Received Radarr event: {payload.get('eventType')}")
        get_radarr_mgr().added_hook(payload)
        return {"ok": True}
    
    @app.post("/sonarr")
    async def sonarr_webhook(request: Request):
        """Handle webhook from Sonarr
        
        Processes events: SeriesAdded, Grab
        """
        payload = await request.json()
        logger.debug(f"[WEBHOOK] Received Sonarr event: {payload.get('eventType')}")
        get_sonarr_mgr().added_hook(payload)
        return {"ok": True}
