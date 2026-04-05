"""Webhook endpoints for Radarr/Sonarr"""

import asyncio
import logging
from typing import Callable
from fastapi import Request

from .app import app
from ..db.repositories.webhook import WebhookRepository

logger = logging.getLogger("ott-hooks")

# Dedup window: *arr webhook retries hit the same endpoint with the same body
# within a few seconds when the first request times out. 60s comfortably covers
# that without discarding genuinely-new events for the same item.
DEDUP_WINDOW_SECONDS = 60


async def _process_arr_webhook(
    payload: dict,
    service: str,
    item_key: str,
    added_hook: Callable[[dict], None],
) -> dict:
    """Shared webhook processing: dedup -> process -> log.

    Both check_duplicate and log_webhook already swallow DB errors internally
    (returning False) so a missing/broken DB degrades gracefully: no dedup,
    no logging, but webhook processing still runs.
    """
    if await asyncio.to_thread(
        WebhookRepository.check_duplicate, payload, DEDUP_WINDOW_SECONDS
    ):
        logger.info(
            f"[WEBHOOK] Duplicate {service} {payload.get('eventType')} within "
            f"{DEDUP_WINDOW_SECONDS}s window, skipping"
        )
        return {"ok": True, "skipped": "duplicate"}

    logger.debug(f"[WEBHOOK] Received {service} event: {payload.get('eventType')}")
    item = payload.get(item_key, {}) or {}
    item_id = item.get("id", 0)
    title = item.get("title", "")
    event_type = payload.get("eventType", "")

    status = "success"
    try:
        await asyncio.to_thread(added_hook, payload)
    except Exception as e:
        logger.error(f"[WEBHOOK] {service} webhook processing failed: {e}", exc_info=True)
        status = "error"
        await asyncio.to_thread(
            WebhookRepository.log_webhook,
            service=service,
            event_type=event_type,
            item_id=item_id,
            title=title,
            payload=payload,
            status=status,
            error_message=str(e),
        )
        return {"ok": False, "error": "Processing failed"}

    # Log success so a retry within the window is deduplicated
    await asyncio.to_thread(
        WebhookRepository.log_webhook,
        service=service,
        event_type=event_type,
        item_id=item_id,
        title=title,
        payload=payload,
        status=status,
    )
    return {"ok": True}


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
        return await _process_arr_webhook(
            payload, "radarr", "movie", get_radarr_mgr().added_hook
        )

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
        return await _process_arr_webhook(
            payload, "sonarr", "series", get_sonarr_mgr().added_hook
        )
