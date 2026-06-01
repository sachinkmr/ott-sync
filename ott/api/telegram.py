"""Telegram callback endpoint for override approvals"""

import hmac
import logging
from typing import Callable, Optional
from fastapi import Request, HTTPException
import requests

from .app import app
from .models import parse_callback_action
from ..constants import COMMAND_MOVIES_SEARCH, COMMAND_SERIES_SEARCH
from ..managers.dead_media import (
    CALLBACK_PREFIX as DEAD_MEDIA_PREFIX,
    VALID_USER_ACTIONS as DEAD_MEDIA_VALID_ACTIONS,
)

logger = logging.getLogger("ott-hooks")

# Header name that Telegram sends the secret_token in. Set the same
# value when calling setWebhook with the secret_token parameter.
_TG_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _tg_answer_callback(
    telegram, callback_query: dict, text: str, show_alert: bool = True,
) -> None:
    """Call Telegram's answerCallbackQuery to dismiss the loading spinner on
    the button and show feedback to the user. Best-effort; failure just
    leaves a spinning button (Telegram drops it after a few seconds).

    `show_alert=True` (default) renders as a modal that stays on screen
    until the user taps OK — appropriate for dead-media actions where the
    feedback explains a side-effect (drop, unmonitor, search). The default
    toast variant (show_alert=False) auto-dismisses after ~5 seconds and is
    easy to miss on mobile.

    The telegram client doesn't expose this directly because it's a
    callback-only operation that needs the callback_query id, which only
    exists inside this handler — keeping it local avoids a wider API change.
    """
    if not getattr(telegram, "enabled", False):
        return
    token = getattr(telegram, "token", None)
    cb_id = callback_query.get("id")
    if not token or not cb_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={
                "callback_query_id": cb_id,
                "text": text[:200],
                "show_alert": show_alert,
            },
            timeout=5,
        )
    except requests.RequestException as e:
        logger.warning(f"[TG-DEAD] answerCallbackQuery failed: {e}")


def _tg_edit_message_text(telegram, chat_id, message_id: int, new_text: str) -> None:
    """Edit the original alert message in place. Used after a dead-media
    action so the inline buttons disappear and the verdict shows instead.

    Removes reply_markup explicitly so the buttons aren't rendered after the
    action lands (otherwise users could re-click and get an
    'already actioned' popup, which is correct but noisy).
    """
    if not getattr(telegram, "enabled", False):
        return
    token = getattr(telegram, "token", None)
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": new_text,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": []},
            },
            timeout=10,
        )
    except requests.RequestException as e:
        logger.warning(f"[TG-DEAD] editMessageText failed: {e}")


def _strip_prior_verdict(text: str) -> str:
    """If the message was already edited (re-click case), drop the previous
    'Action taken:' tail so we don't grow it indefinitely."""
    marker = "\n\n*Action taken:*"
    idx = text.find(marker)
    return text[:idx] if idx >= 0 else text


def register_telegram_routes(
    get_radarr_mgr: Callable,
    get_sonarr_mgr: Callable,
    get_telegram: Callable,
    webhook_secret: str = "",
    get_dead_media_mgr: Optional[Callable] = None,
):
    """Register Telegram callback endpoint

    Args:
        get_radarr_mgr: Callable that returns current RadarrManager instance
        get_sonarr_mgr: Callable that returns current SonarrManager instance
        get_telegram: Callable that returns current TelegramNotifier instance
        webhook_secret: If non-empty, every incoming request must carry a
            matching X-Telegram-Bot-Api-Secret-Token header. Requests
            without it (or with a wrong value) get a 403.
    """

    @app.post("/telegram/callback")
    async def telegram_callback(request: Request):
        """Handle Telegram bot callback (override button clicks and anime confirmations)

        Callback data formats:
        - "override:movie:123" or "override:series:456" - OTT override
        - "approve:movie:123" or "approve:series:456" - Manual approval
        - "anime:confirm:123" - Confirm series is anime
        - "anime:reject:123" - Reject series as anime
        """
        # ── Verify secret token (§2.4) ──
        if webhook_secret:
            header_value = request.headers.get(_TG_SECRET_HEADER, "")
            if not hmac.compare_digest(header_value, webhook_secret):
                logger.warning(
                    f"[TG] Rejected callback: invalid or missing "
                    f"{_TG_SECRET_HEADER} header"
                )
                raise HTTPException(status_code=403, detail="Invalid secret token")

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

            # Serialize against concurrent webhook flow for the same series
            # (same race class that §6.2 fixed for override/approve callbacks).
            with mgr._get_item_lock(series_id):
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

                    # Apply anime metadata (inside lock - it also mutates the series)
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

        # ─────────────────────────────────────────────
        # Dead-media callbacks (Phase B action buttons)
        # ─────────────────────────────────────────────
        # callback_data format: "dead-media:<action>:<notification_id>"
        # e.g. "dead-media:search:42", "dead-media:unmonitor_series:42"
        if callback_data.startswith(DEAD_MEDIA_PREFIX):
            if get_dead_media_mgr is None:
                logger.warning("[TG-DEAD] callback received but dead-media manager not wired")
                _tg_answer_callback(telegram, cb, "❌ Dead-media manager disabled")
                return {"ok": True}

            dm = get_dead_media_mgr()
            if dm is None:
                # Disabled by hot-reload between alert and click.
                _tg_answer_callback(telegram, cb, "❌ Dead-media manager disabled")
                return {"ok": True}

            # Parse "dead-media:<action>:<notif_id>"
            try:
                rest = callback_data[len(DEAD_MEDIA_PREFIX):]
                action, notif_id_str = rest.split(":", 1)
                notification_id = int(notif_id_str)
            except (ValueError, IndexError):
                logger.error(f"[TG-DEAD] Malformed callback_data: {callback_data!r}")
                _tg_answer_callback(telegram, cb, "❌ Bad callback data")
                return {"ok": True}

            if action not in DEAD_MEDIA_VALID_ACTIONS:
                logger.error(f"[TG-DEAD] Invalid action: {action!r}")
                _tg_answer_callback(telegram, cb, "❌ Unknown action")
                return {"ok": True}

            # Run the action. The manager's handle_user_action is idempotent —
            # repeated clicks return the prior outcome instead of re-firing.
            success, verdict_text = dm.handle_user_action(notification_id, action)
            logger.info(
                f"[TG-DEAD] {user} clicked {action} on notif {notification_id} "
                f"→ success={success} verdict={verdict_text}"
            )

            # Acknowledge the click so the spinning button stops.
            _tg_answer_callback(telegram, cb, verdict_text)

            # Edit the original message to show the chosen verdict instead
            # of the inline buttons. Best-effort — if it fails, the verdict
            # is still in the callback popup and the user knows the outcome.
            msg = cb.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            message_id = msg.get("message_id")
            existing_text = msg.get("text", "") or msg.get("caption", "")
            if chat_id and message_id and existing_text:
                # Append the verdict line; strip any prior verdict (re-click case).
                cleaned = _strip_prior_verdict(existing_text)
                new_text = f"{cleaned}\n\n*Action taken:* {verdict_text} — _by {user}_"
                _tg_edit_message_text(
                    telegram, chat_id, message_id, new_text,
                )

            return {"ok": True}

        return {"ok": True}
