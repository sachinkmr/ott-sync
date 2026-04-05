"""Webhook endpoints for Radarr/Sonarr"""

import asyncio
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
        try:
            payload = await request.json()
        except Exception as e:
            logger.error(f"[WEBHOOK] Radarr webhook body parse failed: {e}", exc_info=True)
            return {"ok": False, "error": "Invalid JSON body"}

        logger.debug(f"[WEBHOOK] Received Radarr event: {payload.get('eventType')}")
        try:
            # added_hook is blocking (network + DB I/O); offload so we don't
            # stall the FastAPI event loop while *arr waits for the response.
            await asyncio.to_thread(get_radarr_mgr().added_hook, payload)
        except Exception as e:
            logger.error(f"[WEBHOOK] Radarr webhook processing failed: {e}", exc_info=True)
            return {"ok": False, "error": "Processing failed"}
        return {"ok": True}

    @app.post("/sonarr")
    async def sonarr_webhook(request: Request):
        """Handle webhook from Sonarr

        Processes events: SeriesAdded, Grab
        """
        try:
            payload = await request.json()
        except Exception as e:
            logger.error(f"[WEBHOOK] Sonarr webhook body parse failed: {e}", exc_info=True)
            return {"ok": False, "error": "Invalid JSON body"}

        logger.debug(f"[WEBHOOK] Received Sonarr event: {payload.get('eventType')}")
        try:
            await asyncio.to_thread(get_sonarr_mgr().added_hook, payload)
        except Exception as e:
            logger.error(f"[WEBHOOK] Sonarr webhook processing failed: {e}", exc_info=True)
            return {"ok": False, "error": "Processing failed"}
        return {"ok": True}
