"""Telegram callback endpoint for override approvals"""

import logging
from typing import Callable
from fastapi import Request

from .app import app
from .models import parse_callback_action
from ..constants import COMMAND_MOVIES_SEARCH, COMMAND_SERIES_SEARCH

logger = logging.getLogger("ott-hooks")


def register_telegram_routes(get_radarr_mgr: Callable, get_sonarr_mgr: Callable, get_telegram: Callable):
    """Register Telegram callback endpoint
    
    Args:
        get_radarr_mgr: Callable that returns current RadarrManager instance
        get_sonarr_mgr: Callable that returns current SonarrManager instance
        get_telegram: Callable that returns current TelegramNotifier instance
    """
    
    @app.post("/telegram/callback")
    async def telegram_callback(request: Request):
        """Handle Telegram bot callback (override button clicks and anime confirmations)
        
        Callback data formats:
        - "override:movie:123" or "override:series:456" - OTT override
        - "anime:confirm:123" - Confirm series is anime
        - "anime:reject:123" - Reject series as anime
        """
        payload = await request.json()
        cb = payload.get("callback_query", {})
        callback_data = cb.get("data", "")
        user = cb.get("from", {}).get("first_name", "User")
        
        # Get current telegram instance (supports hot reload)
        telegram = get_telegram()

        # ─────────────────────────────────────────────
        # Anime Callbacks
        # ─────────────────────────────────────────────
        if callback_data.startswith("anime:"):
            parsed = parse_callback_action(callback_data)
            if not parsed:
                logger.error(f"[TG-ANIME] Invalid callback data: {callback_data!r}")
                telegram.send("❌ Invalid callback data")
                return {"ok": True}
            action = parsed.target
            series_id = parsed.item_id

            mgr = get_sonarr_mgr()
            
            # Fetch series data
            res = mgr.client.get(f"series/{series_id}")
            if not res:
                logger.error(f"[TG-ANIME] Failed to fetch series {series_id}")
                telegram.send("❌ Series not found")
                return {"ok": True}
            
            series_data = res.json()
            title = series_data.get("title", "Unknown")
            tags = set(series_data.get("tags", []))
            
            if action == "confirm":
                # User confirmed it's anime
                logger.info(f"[TG-ANIME] {user} confirmed anime: {title} (ID: {series_id})")
                
                # Remove maybe tag, add detected tag
                tags.discard(mgr.anime_maybe_tag)
                tags.add(mgr.anime_detected_tag)
                tags.add(mgr.anime_checked_tag)
                
                series_data["tags"] = list(tags)
                
                # Update series
                update_res = mgr.client.put(f"series/{series_id}", json=series_data)
                if not update_res:
                    telegram.send(f"❌ Failed to update {title}")
                    return {"ok": True}
                
                # Apply anime metadata
                success = mgr.set_anime_metadata(series_id, series_data, force=True)
                
                if success:
                    telegram.send(
                        f"✅ *Anime Confirmed*\n\n"
                        f"📺 *{title}*\n\n"
                        f"Series type set to anime and profile updated by {user}."
                    )
                else:
                    telegram.send(
                        f"⚠️ *Anime Confirmed (Partial)*\n\n"
                        f"📺 *{title}*\n\n"
                        f"Tagged as anime but metadata update failed."
                    )
            
            elif action == "reject":
                # User rejected anime classification
                logger.info(f"[TG-ANIME] {user} rejected anime: {title} (ID: {series_id})")
                
                # Remove anime tags
                tags.discard(mgr.anime_maybe_tag)
                tags.discard(mgr.anime_detected_tag)
                tags.add(mgr.anime_checked_tag)  # Keep checked tag to prevent re-detection
                
                series_data["tags"] = list(tags)
                
                update_res = mgr.client.put(f"series/{series_id}", json=series_data)
                if not update_res:
                    telegram.send(f"❌ Failed to update {title}")
                    return {"ok": True}
                
                telegram.send(
                    f"❌ *Not Anime*\n\n"
                    f"📺 *{title}*\n\n"
                    f"Marked as not anime by {user}. Series will keep current settings."
                )
            
            return {"ok": True}

        # ─────────────────────────────────────────────
        # OTT Override Callbacks (Automatic Mode)
        # ─────────────────────────────────────────────
        if callback_data.startswith("override:"):
            # Parse callback data: "override:movie:123" or "override:series:456"
            parsed = parse_callback_action(callback_data)
            if not parsed or parsed.target not in ("movie", "series"):
                logger.error(f"[TG] Invalid callback data format: {callback_data!r}")
                telegram.send("❌ Invalid callback data")
                return {"ok": True}
            item_type = parsed.target
            item_id = parsed.item_id

            # Select appropriate manager
            mgr = get_radarr_mgr() if item_type == "movie" else get_sonarr_mgr()

            # Serialize against concurrent webhook processing for the same item
            # (otherwise a mid-flight manual-mode PUT can overwrite monitored=True
            # back to False, silently cancelling the approval).
            with mgr._get_item_lock(item_id):
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

                # Cancel any pending verification timer for this item
                mgr._cancel_verification(item_id)

            # Trigger search (outside lock - command endpoint is fire-and-forget)
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

        # ─────────────────────────────────────────────
        # Manual Approval Callbacks (Manual Mode)
        # ─────────────────────────────────────────────
        if callback_data.startswith("approve:"):
            # Parse callback data: "approve:movie:123" or "approve:series:456"
            parsed = parse_callback_action(callback_data)
            if not parsed or parsed.target not in ("movie", "series"):
                logger.error(f"[TG-MANUAL] Invalid callback data format: {callback_data!r}")
                telegram.send("❌ Invalid callback data")
                return {"ok": True}
            item_type = parsed.target
            item_id = parsed.item_id

            # Select appropriate manager
            mgr = get_radarr_mgr() if item_type == "movie" else get_sonarr_mgr()

            # Serialize against concurrent webhook processing for the same item
            # (otherwise a mid-flight manual-mode PUT can overwrite monitored=True
            # back to False, silently cancelling the approval).
            with mgr._get_item_lock(item_id):
                # Fetch current item state
                res = mgr.client.get(f"{item_type}/{item_id}")
                if not res:
                    logger.error(f"[TG-MANUAL] Failed to fetch {item_type} id={item_id}")
                    telegram.send("❌ Failed to approve - item not found")
                    return {"ok": True}

                data = res.json()
                title = data.get("title", "item")

                # Re-monitor the item to allow download
                data["monitored"] = True

                # Update item
                update_res = mgr.client.put(f"{item_type}/{item_id}", json=data)
                if not update_res:
                    logger.error(f"[TG-MANUAL] Failed to update {item_type} id={item_id}")
                    telegram.send("❌ Failed to approve - update failed")
                    return {"ok": True}

            # Trigger search (outside lock - command endpoint is fire-and-forget)
            search_command = COMMAND_MOVIES_SEARCH if item_type == "movie" else COMMAND_SERIES_SEARCH
            cmd_res = mgr.client.post("command", json={
                "name": search_command,
                f"{item_type}Ids": [item_id],
            })

            # Verify command was accepted
            if not cmd_res:
                logger.error(f"[TG-MANUAL] Search command failed for {item_type} id={item_id}")
                telegram.send(
                    f"⚠️ *Approved but search failed*\n\n"
                    f"Item re-monitored, but automatic search could not be triggered.\n"
                    f"Please manually search for {title}."
                )
                return {"ok": True}

            # Send confirmation
            telegram.send(
                f"✅ *Download Approved*\n\n"
                f"Approved by {user}.\n"
                f"Search triggered for {title}."
            )

            logger.info(f"[TG-MANUAL] Download approved by {user} for {item_type} id={item_id}")
            return {"ok": True}

        return {"ok": True}
