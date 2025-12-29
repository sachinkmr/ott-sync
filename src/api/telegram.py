"""Telegram callback endpoint for override approvals"""

import logging
from typing import Callable
from fastapi import Request

from .app import app
from ..constants import COMMAND_MOVIES_SEARCH, COMMAND_SERIES_SEARCH

logger = logging.getLogger("ott-hooks")


def register_telegram_routes(get_radarr_mgr: Callable, get_sonarr_mgr: Callable, telegram):
    """Register Telegram callback endpoint
    
    Args:
        get_radarr_mgr: Callable that returns current RadarrManager instance
        get_sonarr_mgr: Callable that returns current SonarrManager instance
        telegram: TelegramNotifier instance
    """
    
    @app.post("/telegram/callback")
    async def telegram_callback(request: Request):
        """Handle Telegram bot callback (override button clicks)
        
        When user clicks "Download anyway" button in Telegram:
        1. Remove ott-skipped tag
        2. Add ott-override tag
        3. Set monitored = true
        4. Trigger search command
        """
        payload = await request.json()
        cb = payload.get("callback_query", {})
        callback_data = cb.get("data", "")
        user = cb.get("from", {}).get("first_name", "User")

        # Ignore non-override callbacks
        if not callback_data.startswith("override:"):
            return {"ok": True}

        # Parse callback data: "override:movie:123" or "override:series:456"
        try:
            _, item_type, item_id = callback_data.split(":")
            item_id = int(item_id)
        except (ValueError, IndexError) as e:
            logger.error(f"[TG] Invalid callback data format: {callback_data} - {e}")
            telegram.send("❌ Invalid callback data")
            return {"ok": True}

        # Select appropriate manager
        mgr = get_radarr_mgr() if item_type == "movie" else get_sonarr_mgr()
        
        # Fetch current item state
        res = mgr.client.get(f"{item_type}/{item_id}")
        if not res:
            logger.error(f"[TG] Failed to fetch {item_type} id={item_id}")
            telegram.send("❌ Failed to apply override - item not found")
            return {"ok": True}

        data = res.json()
        tags = set(data.get("tags", []))

        # Apply override: remove skipped, add override
        tags.discard(mgr.skipped_tag)
        tags.add(mgr.override_tag)

        data["tags"] = list(tags)
        data["monitored"] = True

        # Update item
        update_res = mgr.client.put(f"{item_type}/{item_id}", json=data)
        if not update_res:
            logger.error(f"[TG] Failed to update {item_type} id={item_id}")
            telegram.send("❌ Failed to apply override - update failed")
            return {"ok": True}

        # Trigger search
        search_command = COMMAND_MOVIES_SEARCH if item_type == "movie" else COMMAND_SERIES_SEARCH
        cmd_res = mgr.client.post("command", json={
            "name": search_command,
            f"{item_type}Ids": [item_id],
        })

        # Verify command was accepted
        if not cmd_res:
            logger.error(f"[TG] Search command failed for {item_type} id={item_id}")
            telegram.send(
                f"⚠️ *Override applied but search failed*\n\n"
                f"Tags updated, but automatic search could not be triggered.\n"
                f"Please manually search for {data.get('title', 'item')}."
            )
            return {"ok": True}

        # Send confirmation
        telegram.send(
            f"⬇️ *Download confirmed*\n\n"
            f"Override applied by {user}.\n"
            f"Search triggered for {data.get('title', 'item')}."
        )

        logger.info(f"[TG] Override applied by {user} for {item_type} id={item_id}")
        return {"ok": True}
