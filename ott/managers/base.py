"""Base manager for OTT-aware governance of *arr services"""

import logging
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from functools import cached_property
from typing import Any

from ..clients.arr_client import ArrClient
from ..clients.justwatch import JustWatchClient
from ..clients.telegram import TelegramNotifier
from ..constants import DEFAULT_THREAD_POOL_SIZE
from ..models import ProcessingMetrics, build_telegram_caption
from ..utils.timestamp_cache import TimestampCache

logger = logging.getLogger("ott-hooks")


class OTTBaseManager(ABC):
    """Base manager for OTT-aware governance
    
    Handles webhook events and scheduled cleanup for Radarr/Sonarr/etc.
    Uses dependency injection for all external services.
    """
    
    def __init__(
        self,
        arr_client: ArrClient,
        justwatch_client: JustWatchClient,
        telegram: TelegramNotifier,
        ott_providers: set[str],
        timestamp_cache: TimestampCache | None = None,
        verification_delay_seconds: int = 60,
        auto_download: bool = False,
        tmdb_client = None,
        anilist_client = None,
    ):
        """Initialize OTT manager
        
        Args:
            arr_client: HTTP client for Radarr/Sonarr API
            justwatch_client: Client for OTT provider lookup
            telegram: Telegram notification client
            ott_providers: Set of allowed OTT provider names
            timestamp_cache: Optional timestamp cache for periodic re-checks
            verification_delay_seconds: Delay before verifying item wasn't grabbed (default 60s)
            auto_download: If False, require manual approval for all items (default: False)
            tmdb_client: Optional TMDBClient for fetching ratings
            anilist_client: Optional AniListClient for fetching anime ratings
        """
        self.client = arr_client
        self.justwatch = justwatch_client
        self.telegram = telegram
        self.ott_providers = ott_providers
        self.timestamp_cache = timestamp_cache or TimestampCache()
        self.verification_delay_seconds = verification_delay_seconds
        self.auto_download = auto_download
        self.tmdb_client = tmdb_client
        self.anilist_client = anilist_client
        self.pool = ThreadPoolExecutor(max_workers=DEFAULT_THREAD_POOL_SIZE)
        self._metrics_lock = threading.Lock()
        self._tag_cache: dict[int, str] = {}  # Cache tag ID -> label mapping
        
        # Item-level locking to prevent race conditions
        self._item_locks: dict[int, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        
        # Track pending verification timers to prevent duplicates
        self._verification_timers: dict[int, threading.Timer] = {}
        self._timers_lock = threading.Lock()
    
    # -------------------- Tags --------------------
    @cached_property
    def skipped_tag(self) -> int:
        """Get or create ott-skipped tag ID"""
        return self._get_or_create_tag("ott-skipped")

    @cached_property
    def processed_tag(self) -> int:
        """Get or create ott-processed tag ID"""
        return self._get_or_create_tag("ott-processed")

    @cached_property
    def override_tag(self) -> int:
        """Get or create ott-override tag ID"""
        return self._get_or_create_tag("ott-override")

    @cached_property
    def anime_checked_tag(self) -> int:
        """Get or create anime-checked tag ID"""
        return self._get_or_create_tag("anime-checked")

    @cached_property
    def anime_detected_tag(self) -> int:
        """Get or create anime-detected tag ID"""
        return self._get_or_create_tag("anime-detected")

    @cached_property
    def anime_maybe_tag(self) -> int:
        """Get or create anime-maybe tag ID"""
        return self._get_or_create_tag("anime-maybe")

    def _normalize_provider_name(self, provider: str) -> str:
        """Normalize provider name to tag format
        
        Args:
            provider: Provider name (e.g., "Netflix", "Prime Video")
            
        Returns:
            Tag-formatted name (e.g., "ott-netflix", "ott-prime-video")
        """
        import re
        # Convert to lowercase and replace spaces/special chars with hyphens
        normalized = re.sub(r'[^a-z0-9]+', '-', provider.lower()).strip('-')
        return f"ott-{normalized}"
    
    def _get_provider_tag(self, provider: str) -> int:
        """Get or create tag ID for OTT provider
        
        Args:
            provider: Provider name (e.g., "Netflix", "Prime Video")
            
        Returns:
            Tag ID for the provider
        """
        tag_label = self._normalize_provider_name(provider)
        return self._get_or_create_tag(tag_label)
    
    def _add_provider_tags(self, item_id: int, providers: list[str]) -> bool:
        """Add provider tags to item
        
        Args:
            item_id: Item ID
            providers: List of provider names
            
        Returns:
            True if tags were added successfully
        """
        if not providers:
            return True
        
        # Fetch current item state
        res = self.client.get(f"{self.item_type()}/{item_id}")
        if not res:
            logger.error(f"[PROVIDER-TAG] Failed to fetch item {item_id}")
            return False
        
        data = res.json()
        current_tags = set(data.get("tags", []))
        
        # Add provider tags
        provider_tags_added = []
        for provider in providers:
            try:
                provider_tag = self._get_provider_tag(provider)
                if provider_tag not in current_tags:
                    current_tags.add(provider_tag)
                    provider_tags_added.append(self._normalize_provider_name(provider))
            except Exception as e:
                logger.error(f"[PROVIDER-TAG] Failed to create tag for {provider}: {e}")
        
        if provider_tags_added:
            data["tags"] = list(current_tags)
            update_res = self.client.put(f"{self.item_type()}/{item_id}", json=data)
            if update_res:
                logger.info(f"[PROVIDER-TAG] Added tags: {', '.join(provider_tags_added)}")
                return True
            else:
                logger.error(f"[PROVIDER-TAG] Failed to update item {item_id}")
                return False
        
        return True
    
    def _cleanup_provider_tags(self, item_id: int, current_providers: list[str]) -> None:
        """Remove provider tags that are no longer valid
        
        Removes tags for providers that are:
        - Not in the configured OTT providers list, OR
        - Not returned by current JustWatch lookup
        
        Args:
            item_id: Item ID
            current_providers: List of currently available providers from JustWatch
        """
        # Fetch current item state
        res = self.client.get(f"{self.item_type()}/{item_id}")
        if not res:
            return
        
        data = res.json()
        tags = set(data.get("tags", []))
        
        # Get all provider tag IDs and their names
        res = self.client.get("tag")
        if not res:
            return
        
        all_tags = {tag["id"]: tag["label"] for tag in res.json()}
        
        # Normalize current providers for comparison
        current_provider_tags = {self._normalize_provider_name(p) for p in current_providers}
        
        # Normalize configured providers for comparison
        configured_provider_tags = {self._normalize_provider_name(p) for p in self.ott_providers}
        
        # Find provider tags to remove
        tags_to_remove = []
        for tag_id in list(tags):
            tag_label = all_tags.get(tag_id, "")
            # Check if it's a provider tag (starts with "ott-" but not our special tags)
            if tag_label.startswith("ott-") and tag_label not in [
                "ott-skipped", "ott-processed", "ott-override"
            ]:
                # Remove only orphaned tags: not in current availability AND not in our
                # configured providers. A tag that's either currently available OR tracked
                # in config represents real state we want to keep.
                if tag_label not in current_provider_tags and tag_label not in configured_provider_tags:
                    tags_to_remove.append((tag_id, tag_label))
        
        if tags_to_remove:
            for tag_id, tag_label in tags_to_remove:
                tags.discard(tag_id)
            
            data["tags"] = list(tags)
            update_res = self.client.put(f"{self.item_type()}/{item_id}", json=data)
            if update_res:
                removed_names = [label for _, label in tags_to_remove]
                logger.info(f"[PROVIDER-TAG] Removed stale tags: {', '.join(removed_names)}")
            else:
                logger.error(f"[PROVIDER-TAG] Failed to cleanup tags for item {item_id}")

    def _get_item_lock(self, item_id: int) -> threading.Lock:
        """Get or create lock for specific item
        
        Args:
            item_id: Item ID to lock
            
        Returns:
            Lock object for this item
        """
        with self._locks_lock:
            if item_id not in self._item_locks:
                self._item_locks[item_id] = threading.Lock()
            return self._item_locks[item_id]
    
    def _schedule_verification(self, item_id: int, title: str) -> None:
        """Schedule delayed verification check for item
        
        Prevents duplicate timers for the same item.
        
        Args:
            item_id: Item ID to verify
            title: Item title for logging
        """
        with self._timers_lock:
            # Cancel existing timer if present (shouldn't happen but safety check)
            if item_id in self._verification_timers:
                old_timer = self._verification_timers[item_id]
                if old_timer.is_alive():
                    logger.warning(
                        f"[VERIFY] Canceling existing verification timer for id={item_id}"
                    )
                    old_timer.cancel()
            
            # Schedule new verification
            logger.info(
                f"[VERIFY] Scheduling verification in {self.verification_delay_seconds}s "
                f"for {self.item_type()} id={item_id}"
            )
            timer = threading.Timer(
                self.verification_delay_seconds,
                self._verify_and_cleanup,
                args=[item_id, title]
            )
            timer.daemon = True
            self._verification_timers[item_id] = timer
            timer.start()
    
    def _cancel_verification(self, item_id: int) -> None:
        """Cancel pending verification for item (e.g., when override applied)
        
        Args:
            item_id: Item ID
        """
        with self._timers_lock:
            if item_id in self._verification_timers:
                timer = self._verification_timers[item_id]
                if timer.is_alive():
                    timer.cancel()
                    logger.info(f"[VERIFY] Canceled verification for id={item_id}")
                del self._verification_timers[item_id]
    
    def _verify_and_cleanup(self, item_id: int, title: str) -> None:
        """Verify item and cleanup timer tracking
        
        Args:
            item_id: Item ID
            title: Item title
        """
        try:
            self._verify_not_grabbed(item_id, title)
        finally:
            # Clean up timer tracking
            with self._timers_lock:
                if item_id in self._verification_timers:
                    del self._verification_timers[item_id]
    
    def _verify_not_grabbed(self, item_id: int, title: str) -> None:
        """Verify item wasn't grabbed during OTT check (delayed verification)
        
        Checks if blocked item somehow ended up in download queue or has files.
        This catches race conditions where auto-grab happens during JustWatch lookup.
        
        Args:
            item_id: Item ID to verify
            title: Item title for logging
        """
        logger.info(f"[VERIFY] Checking if {self.item_type()} id={item_id} slipped through")
        
        with self._get_item_lock(item_id):
            # Fetch current item state
            res = self.client.get(f"{self.item_type()}/{item_id}")
            if not res:
                logger.warning(
                    f"[VERIFY] Item id={item_id} not found - may have been deleted. "
                    f"Skipping verification."
                )
                return
            
            data = res.json()
            tags = set(data.get("tags", []))
            
            # 🔒 Absolute override guard - NEVER touch items with override tag
            if self.override_tag in tags:
                logger.info(
                    f"[VERIFY] ott-override present → skip verification for id={item_id} "
                    f"(user approved download)"
                )
                return
            
            # Only verify if item has ott-skipped tag (was blocked)
            if self.skipped_tag not in tags:
                logger.debug(f"[VERIFY] Item {item_id} not blocked, skipping verification")
                return
            
            # Check if item has files (download completed or in progress)
            has_files = data.get("hasFile", False)
            
            # Check if item is in download queue
            in_queue = False
            queue_res = self.client.get("queue")
            if queue_res:
                queue_items = queue_res.json().get("records", [])
                in_queue = any(
                    q.get(f"{self.item_type()}Id") == item_id 
                    for q in queue_items
                )
            
            # If item slipped through (has files or in queue), enforce block
            if has_files or in_queue:
                logger.error(
                    f"[VERIFY] ⚠️ Race condition detected! "
                    f"{self.item_type()} id={item_id} ({title}) slipped through - "
                    f"has_files={has_files}, in_queue={in_queue}"
                )
                
                # 🔒 FINAL override check before re-enforcement
                # User may have approved while verification was waiting
                final_check = self.client.get(f"{self.item_type()}/{item_id}")
                if final_check:
                    final_tags = set(final_check.json().get("tags", []))
                    if self.override_tag in final_tags:
                        logger.info(
                            f"[VERIFY] Override applied during verification window → "
                            f"skip re-enforcement for id={item_id}"
                        )
                        return
                
                # Re-enforce block (cancel queue, delete files)
                self.enforce_block(item_id, delete_files=True)
                
                # Alert via Telegram
                self.telegram.send(
                    f"⚠️ *Race condition caught*\n\n"
                    f"🎬 *{title}*\n\n"
                    f"Item slipped through OTT block but was caught before completion.\n"
                    f"Download canceled automatically.\n\n"
                    f"📊 *Status:*\n"
                    f"  • In queue: {in_queue}\n"
                    f"  • Has files: {has_files}"
                )
                
                logger.info(f"[VERIFY] ✓ Re-enforced block for {item_id}")
            else:
                logger.info(f"[VERIFY] ✓ Item {item_id} correctly blocked (not in queue/files)")
    
    def _get_or_create_tag(self, label: str) -> int:
        """Get existing tag ID or create new tag
        
        Args:
            label: Tag label
            
        Returns:
            Tag ID
        """
        res = self.client.get("tag")
        if not res:
            raise RuntimeError(f"Failed to fetch tags from {self.item_type()} API")
        
        tags = res.json()
        for tag in tags:
            if tag["label"] == label:
                return tag["id"]
        
        # Create new tag
        res = self.client.post("tag", json={"label": label})
        if not res:
            raise RuntimeError(f"Failed to create tag '{label}'")
        
        return res.json()["id"]
    
    def _get_tag_labels(self, tag_ids: list[int]) -> dict[int, str]:
        """Get tag labels for given tag IDs with caching
        
        Args:
            tag_ids: List of tag IDs to resolve
            
        Returns:
            Dictionary mapping tag ID to label
        """
        # Check which tags need fetching
        missing_ids = [tid for tid in tag_ids if tid not in self._tag_cache]
        
        if missing_ids:
            # Fetch all tags from API (refresh cache)
            res = self.client.get("tag")
            if res:
                for tag in res.json():
                    self._tag_cache[tag["id"]] = tag["label"]
        
        # Return requested tag labels
        return {tid: self._tag_cache.get(tid, f"unknown-{tid}") for tid in tag_ids}
    
    def _extract_plex_users(self, tag_ids: list[int]) -> list[str]:
        """Extract Plex usernames from pulsarr tags
        
        Args:
            tag_ids: List of tag IDs from item
            
        Returns:
            List of Plex usernames (empty if none found)
        """
        if not tag_ids:
            return []
        
        tag_labels = self._get_tag_labels(tag_ids)
        users = []
        
        for tag_id, label in tag_labels.items():
            # Parse format: pulsarr-user-{username}
            if label.startswith("pulsarr-user-"):
                username = label.replace("pulsarr-user-", "")
                if username:  # Ensure not empty
                    users.append(username)
        
        return users
    
    def _fetch_ratings(self, item: dict[str, Any]) -> dict[str, float | None]:
        """Fetch ratings from available sources (TMDb, AniList).

        IMDb is not supported - TMDb exposes the imdb_id but not the score,
        which would require a separate OMDb API integration (deferred).

        Args:
            item: Item data from webhook payload

        Returns:
            Dict of source -> rating (0-10 scale): {"tmdb": 8.5, "anilist": 8.2}.
            Keys are always present; values are None when the fetch failed or
            the source is not applicable.
        """
        ratings: dict[str, float | None] = {"tmdb": None, "anilist": None}

        tmdb_id = item.get("tmdbId")

        # Fetch TMDb ratings
        if self.tmdb_client and tmdb_id:
            try:
                if self.item_type() == "movie":
                    tmdb_data = self.tmdb_client.get_movie_ratings(tmdb_id)
                else:  # series
                    tmdb_data = self.tmdb_client.get_series_ratings(tmdb_id)
                if tmdb_data:
                    ratings["tmdb"] = tmdb_data.get("tmdb")
            except Exception as e:
                logger.error(f"[RATINGS] TMDb fetch error: {e}")

        # AniList ratings are only available when we know the AniList ID, which
        # we don't expose from the anime_detector yet. Phase 7.3 will wire this.
        return ratings
    
    def _extract_plex_user(self, item: dict[str, Any]) -> str | None:
        """Extract Plex user from item metadata
        
        Plex watchlist items may have user info in tags or custom fields.
        
        Args:
            item: Item dictionary from webhook payload
            
        Returns:
            Plex username if found, None otherwise
        """
        # Check for Plex-specific fields in item metadata
        # (These field names may vary based on your Plex/Radarr/Sonarr integration)
        return (
            item.get("plexUser")
            or item.get("addedBy")
            or item.get("source", {}).get("user") if isinstance(item.get("source"), dict) else None
        )

    def _cancel_queue_items_for(self, item_id: int) -> int:
        """Cancel any queued downloads for the given *arr item.

        *arr queue DELETE requires the queue-record id (not movieId/seriesId),
        so we fetch the queue, filter by item_id, and delete each matching
        record. For Sonarr series this cancels ALL queued episodes of the
        series. blocklist=false is enforced so releases remain re-grabbable.

        Non-fatal: failures are logged but never raised.

        Args:
            item_id: Radarr movie id or Sonarr series id.

        Returns:
            Number of queue records successfully deleted.
        """
        id_field = f"{self.item_type()}Id"  # "movieId" or "seriesId"
        try:
            records = self.client.get_queue()
        except Exception as e:
            logger.error(f"[QUEUE] Failed to fetch queue for id={item_id}: {e}")
            return 0

        matching_ids = [r["id"] for r in records if r.get(id_field) == item_id]
        if not matching_ids:
            return 0

        # Also fire CancelPendingDownloads command so *arr releases any reserved
        # slot - the queue DELETE removes the record but the search/grab command
        # may still be in flight.
        try:
            self.client.post("command", json={
                "name": "CancelPendingDownloads",
                f"{self.item_type()}Ids": [item_id],
            })
        except Exception as e:
            logger.warning(f"[QUEUE] CancelPendingDownloads failed for id={item_id}: {e}")

        cancelled = 0
        for queue_id in matching_ids:
            if self.client.delete_queue_item(queue_id, remove_from_client=True, blocklist=False):
                cancelled += 1

        if cancelled:
            logger.info(
                f"[QUEUE] Cancelled {cancelled} queue item(s) for "
                f"{self.item_type()} id={item_id}"
            )
        return cancelled

    # -------------------- Enforcement --------------------
    def enforce_block(self, item_id: int, delete_files: bool = True) -> None:
        """Block item from downloading
        
        Actions:
        - Cancel pending downloads
        - Remove from download queue
        - Unmonitor item
        - Add ott-skipped and ott-processed tags
        - Optionally delete files from disk
        
        Args:
            item_id: Radarr/Sonarr item ID
            delete_files: Whether to delete files from disk
        """
        logger.warning(f"[ACTION] Blocking {self.item_type()} id={item_id}")

        # Cancel any queued downloads (uses queue-record ids, blocklist=false)
        self._cancel_queue_items_for(item_id)

        # Get current item state
        res = self.client.get(f"{self.item_type()}/{item_id}")
        if not res:
            logger.error(f"[ACTION] Failed to fetch item for enforcement - aborting")
            return

        # Update item: unmonitor and tag
        data = res.json()
        data["monitored"] = False
        data["tags"] = list(
            set(data.get("tags", [])) | {self.skipped_tag, self.processed_tag}
        )

        update_res = self.client.put(f"{self.item_type()}/{item_id}", json=data)
        if not update_res:
            logger.error(f"[ACTION] Failed to update item tags/monitoring - continuing with cleanup")
            # Continue anyway - at least cancel downloads and delete files
        else:
            logger.info("[ACTION] Item unmonitored + tagged")
        
        # Delete files from disk to save space (but keep the item in Radarr/Sonarr)
        if delete_files:
            # Get the movie/series file IDs
            file_key = "movieFile" if self.item_type() == "movie" else "episodeFile"
            files = data.get(file_key) if self.item_type() == "movie" else []
            
            if self.item_type() == "series":
                # For series, need to fetch episodes to get episodeFile IDs
                episodes_res = self.client.get("episode", params={"seriesId": item_id})
                if episodes_res:
                    files = [ep.get("episodeFile") for ep in episodes_res.json() if ep.get("hasFile")]
            else:
                # For movie, movieFile is directly in the data
                files = [data.get("movieFile")] if data.get("hasFile") else []
            
            # Delete each file individually
            deleted_count = 0
            for file_obj in files:
                if file_obj and isinstance(file_obj, dict):
                    file_id = file_obj.get("id")
                    if file_id:
                        endpoint = "moviefile" if self.item_type() == "movie" else "episodefile"
                        delete_res = self.client.delete(f"{endpoint}/{file_id}")
                        if delete_res:
                            deleted_count += 1
            
            if deleted_count > 0:
                logger.info(f"[ACTION] {deleted_count} file(s) deleted from disk")
            elif files:
                logger.warning("[ACTION] File deletion failed")
            else:
                logger.info("[ACTION] No files to delete")

    # -------------------- Webhook --------------------
    def added_hook(self, payload: dict[str, Any]) -> None:
        """Handle webhook event from Radarr/Sonarr
        
        Args:
            payload: Webhook payload containing event type and item data
        """
        import json
        
        event = payload.get("eventType")
        item = payload.get(self.item_type(), {})
    
        title = item.get("title")
        year = item.get("year")
        item_id = item.get("id")
        tags = set(item.get("tags", []))
    
        # 📊 Log webhook payload structure for debugging
        logger.info(f"[WEBHOOK] {event} → {title} ({year}) id={item_id}")
        logger.info(f"[WEBHOOK] Payload keys: {list(payload.keys())}")
        logger.info(f"[WEBHOOK] Item keys: {list(item.keys())}")
        logger.debug(f"[WEBHOOK] Full payload:\n{json.dumps(payload, indent=2, default=str)}")
    
        # Handle Add/Grab events (flexible matching for variants)
        if not event.startswith(("MovieAdd", "SeriesAdd", "Grab", "Download")):
            logger.debug(f"[WEBHOOK] Ignoring event type: {event}")
            return
    
        # 🔒 Absolute override guard (check before acquiring lock)
        if self.override_tag in tags:
            logger.info(
                f"[DECISION] ott-override present → skip ALL OTT enforcement "
                f"for {self.item_type()} id={item_id}"
            )
            return
        
        # 🔐 Acquire item-level lock to prevent race conditions
        with self._get_item_lock(item_id):
            logger.debug(f"[LOCK] Acquired lock for {self.item_type()} id={item_id}")
            self._process_webhook_locked(payload, event, item, title, year, item_id, tags)
    
    def _process_webhook_locked(self, payload: dict, event: str, item: dict, 
                                  title: str, year: int, item_id: int, tags: set) -> None:
        """Process webhook with item lock held
        
        Args:
            payload: Full webhook payload
            event: Event type
            item: Item data
            title: Item title
            year: Item year
            item_id: Item ID
            tags: Item tags
        """
        # Re-fetch current item state (may have changed since webhook fired)
        res = self.client.get(f"{self.item_type()}/{item_id}")
        if not res:
            logger.error(f"[WEBHOOK] Failed to fetch current state for id={item_id}")
            return
        
        current_item = res.json()
        current_tags = set(current_item.get("tags", []))
        was_monitored = current_item.get("monitored", False)
        
        # Re-check override tag (may have been added since initial check)
        if self.override_tag in current_tags:
            logger.info(f"[DECISION] ott-override added → skip enforcement for id={item_id}")
            return
    
        if event == "Grab":
            logger.info("[DECISION] Grab event → checking OTT as fallback")
    
        # ═══════════════════════════════════════════════════════════════════
        # MANUAL MODE: Require approval for ALL items
        # ═══════════════════════════════════════════════════════════════════
        if not self.auto_download:
            logger.info(f"[MANUAL-MODE] Processing item in manual approval mode")
            
            # Skip if item is monitored (already approved/downloading)
            if was_monitored:
                logger.info(f"[MANUAL-MODE] Item is monitored (approved), skipping")
                return
            
            # Skip if already has override tag (approved but not yet monitored)
            if self.override_tag in current_tags:
                logger.info(f"[MANUAL-MODE] Item has override tag (approved), skipping")
                return
            
            # For Grab events on already-notified items: cancel queue without
            # re-notifying. Grab-before-Add ordering (rare but possible when
            # webhooks race) falls through to the full flow below - it will
            # cancel the queue AND send the notification, so the user still
            # gets prompted for approval.
            if event == "Grab" and self.processed_tag in current_tags:
                logger.info(f"[MANUAL-MODE] Grab event for already-notified item - removing from queue only")
                self._cancel_queue_items_for(item_id)
                return
            
            # Skip if already processed (notification already sent for Add events)
            if self.processed_tag in current_tags:
                logger.info(f"[MANUAL-MODE] Item already notified, skipping")
                return
            
            # 1. Unmonitor item immediately to prevent auto-grab (if not already unmonitored)
            if current_item.get("monitored", False):
                logger.info(f"[MANUAL-MODE] Unmonitoring item id={item_id}")
                current_item["monitored"] = False
                update_res = self.client.put(f"{self.item_type()}/{item_id}", json=current_item)
                if not update_res:
                    logger.error(f"[MANUAL-MODE] Failed to unmonitor item, aborting")
                    return
            
            # 2. Remove from download queue if already added
            logger.info(f"[MANUAL-MODE] Removing from download queue if present")
            self._cancel_queue_items_for(item_id)
            
            # 3. Fetch ratings from all sources
            ratings = self._fetch_ratings(item)
            
            # 4. Check OTT availability (informational only)
            tmdb_id = item.get("tmdbId")
            imdb_id = item.get("imdbId")
            
            providers = self.justwatch.get_providers(
                title, 
                year, 
                self.ott_providers,
                tmdb_id=tmdb_id,
                imdb_id=imdb_id
            )
            
            provider_name = providers[0] if providers else None
            
            # 5. Extract poster and requester info
            images = item.get("images", [])
            poster = next(
                (img.get("remoteUrl") for img in images if img.get("coverType") == "poster"),
                None,
            )
            
            plex_users = self._extract_plex_users(list(tags))
            if plex_users:
                requested_by = f"{', '.join(plex_users)} (Plex)"
            else:
                requested_by = "Automated"
            
            # 6. Build notification caption with ratings
            caption = build_telegram_caption(
                title=title,
                year=year,
                provider=provider_name,
                region=self.telegram.region,
                item_type=self.item_type(),
                item_id=item_id,
                requested_by=requested_by,
                tmdb_rating=ratings.get("tmdb"),
                anilist_rating=ratings.get("anilist"),
                manual_mode=True,
            )
            
            # 7. Send notification with approve button
            buttons = [[{
                "text": "✅ Approve Download",
                "callback_data": f"approve:{self.item_type()}:{item_id}"
            }]]
            
            notification_success = False
            if poster:
                notification_success = self.telegram.send_photo(poster, caption, buttons=buttons)
            else:
                notification_success = self.telegram.send(caption, buttons=buttons)
            
            if not notification_success:
                logger.error(f"[MANUAL-MODE] Telegram notification failed for id={item_id}")
            
            # 8. Mark as processed. Refetch immediately before PUT so that if
            # the approve callback runs concurrently, its monitored=True is
            # preserved in our PUT body (we copy the full object as fetched
            # here). The api/telegram.py callbacks also acquire the same
            # _get_item_lock as this webhook path, so in practice the callback
            # waits for us to finish - this refetch is belt-and-braces.
            res = self.client.get(f"{self.item_type()}/{item_id}")
            if res:
                data = res.json()
                data["tags"] = list(set(data.get("tags", [])) | {self.processed_tag})
                self.client.put(f"{self.item_type()}/{item_id}", json=data)
            
            logger.info(f"[MANUAL-MODE] Item unmonitored, awaiting approval")
            return
        
        # ═══════════════════════════════════════════════════════════════════
        # AUTOMATIC MODE: Only block if found on OTT (current behavior)
        # ═══════════════════════════════════════════════════════════════════
        
        # ─────────────────────────────────────────────
        # Pre-emptive monitoring disable (race condition protection)
        # ─────────────────────────────────────────────
        # Disable monitoring immediately to prevent auto-grab during JustWatch lookup
        # Will restore if OTT not found
        if was_monitored and self.processed_tag not in current_tags:
            logger.info(f"[RACE-PROTECTION] Pre-emptively disabling monitoring for id={item_id}")
            current_item["monitored"] = False
            update_res = self.client.put(f"{self.item_type()}/{item_id}", json=current_item)
            if not update_res:
                logger.error(
                    f"[RACE-PROTECTION] Failed to disable monitoring for id={item_id}. "
                    f"Item may auto-grab during JustWatch lookup. Failing open - defer decision."
                )
                # Fail-open: if we can't disable monitoring, don't proceed with OTT check
                # Item might auto-grab during lookup, so defer to next cron run
                return
    
        # ─────────────────────────────────────────────
        # OTT lookup decision (JustWatch only)
        # ─────────────────────────────────────────────
        providers = None
        was_previously_blocked = False
    
        if self.processed_tag in current_tags:
            logger.info("[DECISION] ott_processed present → skip JustWatch lookup")
            
            # 🚨 Check if item was previously blocked (has ott-skipped tag)
            if self.skipped_tag in tags:
                was_previously_blocked = True
                logger.warning(
                    f"[DECISION] ott-skipped present → item was previously blocked. "
                    f"Re-enforcing block for {self.item_type()} id={item_id}"
                )
                # Re-use previous OTT detection for Telegram notification
                providers = ["OTT"]  # Placeholder to trigger notification flow
        else:
            # Extract TMDb/IMDb IDs for better matching accuracy
            tmdb_id = item.get("tmdbId")
            imdb_id = item.get("imdbId")
            
            providers = self.justwatch.get_providers(
                title, 
                year, 
                self.ott_providers,
                tmdb_id=tmdb_id,
                imdb_id=imdb_id
            )
    
            # 🌐 Infra failure → fail open, retry later
            if providers is None:
                logger.warning("[DECISION] OTT lookup failed → defer decision")
                return
    
        # ─────────────────────────────────────────────
        # Telegram + enforcement decision
        # ─────────────────────────────────────────────
        if providers:
            # Fetch ratings for automatic mode as well
            ratings = self._fetch_ratings(item)
            
            # 🖼 Extract poster if available
            images = item.get("images", [])
            poster = next(
                (img.get("remoteUrl") for img in images if img.get("coverType") == "poster"),
                None,
            )
    
            # 👤 Extract requester info (Plex users or automated)
            plex_users = self._extract_plex_users(list(tags))
            
            if plex_users:
                # Multiple Plex users can watchlist the same item
                users_str = ", ".join(plex_users)
                requested_by = f"{users_str} (Plex)"
                logger.info(f"[WEBHOOK] Plex watchlist by: {users_str}")
            else:
                # No Plex tags - manual add or list import
                requested_by = "Automated"
                logger.info(f"[WEBHOOK] Source: Automated (manual/list)")
    
            caption = build_telegram_caption(
                title=title,
                year=year,
                provider=providers[0],
                region=self.telegram.region,
                item_type=self.item_type(),
                item_id=item_id,
                requested_by=requested_by,
                tmdb_rating=ratings.get("tmdb"),
                anilist_rating=ratings.get("anilist"),
                manual_mode=False,
            )
    
            buttons = [[{
                "text": "⬇️ Download anyway",
                "callback_data": f"override:{self.item_type()}:{item_id}"
            }]]
    
            # 📣 Best-effort Telegram notification (doesn't affect blocking decision)
            notification_success = False
            if was_previously_blocked:
                # Re-blocking scenario - use full caption with ratings
                reblock_caption = (
                    f"🚨 *Re-block detected*\n\n"
                    f"🎬 *{title}*{f' ({year})' if year else ''}\n\n"
                    f"📺 *Available on:* {providers[0]} ({self.telegram.region})\n"
                )
                
                # Add ratings if available
                ratings_parts = []
                if ratings.get("tmdb"):
                    ratings_parts.append(f"⭐ TMDb: {ratings['tmdb']:.1f}/10")
                if ratings.get("anilist"):
                    ratings_parts.append(f"⭐ AniList: {ratings['anilist']:.1f}/10")
                
                if ratings_parts:
                    reblock_caption += "\n".join(ratings_parts) + "\n"
                
                reblock_caption += (
                    f"\n"
                    f"This item was previously blocked but a download was attempted.\n"
                    f"Use the button below to approve if this was intentional."
                )
                
                if poster:
                    notification_success = self.telegram.send_photo(poster, reblock_caption, buttons=buttons)
                else:
                    notification_success = self.telegram.send(reblock_caption, buttons=buttons)
            elif poster:
                notification_success = self.telegram.send_photo(poster, caption, buttons=buttons)
            else:
                notification_success = self.telegram.send(caption, buttons=buttons)

            # ⚠️ Log notification failure but still enforce block
            # Primary unblock method: Telegram callback button (when notification works)
            # Fallback: Manual intervention in Radarr/Sonarr UI (if Telegram is down)
            if not notification_success:
                logger.error(
                    f"[DECISION] Telegram notification FAILED for {self.item_type()} id={item_id}. "
                    f"Blocking anyway - user won't receive unblock button. "
                    f"Manual intervention required via Radarr/Sonarr UI if this persists."
                )
            
            logger.warning(
                f"[DECISION] OTT found on {providers[0]} → enforcing block "
                f"{self.item_type()} id={item_id}"
            )

            # 🔐 FINAL override check before enforcement
            # User may have approved between re-fetch and now
            final_check = self.client.get(f"{self.item_type()}/{item_id}")
            if final_check:
                final_tags = set(final_check.json().get("tags", []))
                if self.override_tag in final_tags:
                    logger.info(
                        f"[RACE-PROTECTION] Override tag added during processing → "
                        f"skip enforcement for id={item_id}"
                    )
                    return

            # �🚫 Enforce block (delete files only if NOT previously blocked)
            self.enforce_block(item_id, delete_files=not was_previously_blocked)            
            # 🏷️ Add provider tags for tracking
            self._add_provider_tags(item_id, providers)
            # 🕒 Update timestamp for periodic re-check
            self.timestamp_cache.update_check(self.item_type(), item_id)
            
            # ⏱️ Schedule delayed verification to catch race conditions
            # ONLY for newly blocked items (not re-blocks) to avoid duplicate timers
            if self.verification_delay_seconds > 0 and not was_previously_blocked:
                self._schedule_verification(item_id, title)

            # ✅ Mark processed AFTER a successful decision
            if self.processed_tag not in tags:
                res = self.client.get(f"{self.item_type()}/{item_id}")
                if res:
                    data = res.json()
                    data["tags"] = list(set(data.get("tags", [])) | {self.processed_tag})
                    self.client.put(f"{self.item_type()}/{item_id}", json=data)
        else:
            # ❌ Not on OTT → mark processed and restore monitoring
            logger.info("[DECISION] Not on OTT → marking processed and restoring monitoring")
            
            # 🕒 Update timestamp even for not-found items
            self.timestamp_cache.update_check(self.item_type(), item_id)
    
            if self.processed_tag not in current_tags:
                res = self.client.get(f"{self.item_type()}/{item_id}")
                if res:
                    data = res.json()
                    data["tags"] = list(set(data.get("tags", [])) | {self.processed_tag})
                    # Restore monitoring if it was pre-emptively disabled
                    if was_monitored and not data.get("monitored"):
                        data["monitored"] = True
                        logger.info(f"[RACE-PROTECTION] Restoring monitoring for id={item_id}")
                    self.client.put(f"{self.item_type()}/{item_id}", json=data)
    
    # -------------------- Cron --------------------
    def cron_cleanup(self) -> ProcessingMetrics:
        """Scheduled cleanup - check all monitored items
        
        Returns:
            Processing metrics
        """
        logger.info(f"[CRON] Starting cleanup for {self.item_type()}")
        metrics = ProcessingMetrics()
        items = self.fetch_items()

        for item in items:
            item_id = item.get("id")
            
            # 🔐 Acquire item-level lock to prevent race with webhooks
            with self._get_item_lock(item_id):
                self._process_cron_item_locked(item, metrics)
        
        logger.info(f"[CRON] Completed → {metrics}")
        return metrics
    
    def _process_cron_item_locked(self, item: dict, metrics: ProcessingMetrics) -> None:
        """Process single item in cron with lock held
        
        Args:
            item: Item data from fetch_items
            metrics: Metrics object to update
        """
        tags = set(item.get("tags", []))
        item_id = item.get("id")
        
        # 🔒 Respect override - never touch items with ott-override
        if self.override_tag in tags:
            metrics.skipped_override += 1
            return
        
        # Skip unmonitored items
        if not item.get("monitored"):
            return
        
        # 🔄 Check processed_tag BEFORE JustWatch to avoid collision with webhook
        # BUT allow re-check if item hasn't been checked in 30+ days (OTT availability changes)
        if self.processed_tag in tags and self.skipped_tag not in tags:
            # Check if we should re-check this item (30-day period)
            if not self.timestamp_cache.should_recheck(self.item_type(), item_id, recheck_days=30):
                metrics.already_processed += 1
                return
            else:
                logger.info(f"[CRON] Re-checking {item_id} (30+ days since last check)")
        
        # 🕵️ Manual unmonitor bypass detection - re-enforce block if monitored with ott-skipped
        if item.get("monitored") and self.skipped_tag in tags:
            logger.warning(f"[CRON] Manual unmonitor bypass detected for {item_id}")
            metrics.cleaned += 1
            self.enforce_block(item_id, delete_files=False)  # Re-block without deleting files
            self.timestamp_cache.update_check(self.item_type(), item_id)
            return

        metrics.checked += 1
        
        # Extract TMDb/IMDb IDs for accurate matching
        tmdb_id = item.get("tmdbId")
        imdb_id = item.get("imdbId")
        
        providers = self.justwatch.get_providers(
            item.get("title"), 
            item.get("year"),
            self.ott_providers,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id
        )
        
        if providers is None:
            logger.warning("[DECISION] OTT lookup failed → defer decision")
            return   # Skip this item
        
        if providers:
            metrics.cleaned += 1
            # 🗑️ Cron also deletes files (saves disk space)
            self.enforce_block(item_id, delete_files=True)
            
            # 🏷️ Update provider tags (add new ones, remove stale ones)
            self._cleanup_provider_tags(item_id, providers)
            self._add_provider_tags(item_id, providers)
            
            self.timestamp_cache.update_check(self.item_type(), item_id)
        else:
            metrics.marked_processed += 1
            
            # 🏷️ Clean up provider tags since item is not on OTT anymore
            self._cleanup_provider_tags(item_id, [])
            
            res = self.client.get(f"{self.item_type()}/{item_id}")
            if res:
                data = res.json()
                data["tags"] = list(
                    set(data.get("tags", [])) | {self.processed_tag}
                )
                self.client.put(f"{self.item_type()}/{item_id}", json=data)
                self.timestamp_cache.update_check(self.item_type(), item_id)

    def migrate_ott_tags(self, unmonitor: bool = False) -> dict[str, int]:
        """Migrate all items to add OTT provider tags
        
        Scans all items (monitored and unmonitored) and adds:
        - Provider tags (ott-netflix, ott-prime-video, etc.)
        - ott-skipped tag (indicates item was blocked)
        - ott-processed tag (indicates item was checked)
        
        Args:
            unmonitor: If True, also unmonitor items found on OTT (default: False)
        
        Returns:
            Dictionary with migration statistics
        """
        logger.info(f"[OTT-MIGRATE] Starting provider tag migration for {self.item_type()}")
        
        stats = {
            'total': 0,
            'tagged': 0,
            'not_on_ott': 0,
            'already_tagged': 0,
            'errors': 0,
            'skipped_override': 0
        }
        
        # Fetch ALL items (not just monitored)
        items = self.fetch_items()
        stats['total'] = len(items)
        
        logger.info(f"[OTT-MIGRATE] Processing {stats['total']} items...")
        
        for idx, item in enumerate(items, 1):
            item_id = item.get("id")
            title = item.get("title", "Unknown")
            year = item.get("year")
            tags = set(item.get("tags", []))
            
            # Progress logging every 50 items
            if idx % 50 == 0:
                logger.info(f"[OTT-MIGRATE] Progress: {idx}/{stats['total']} items processed")
            
            try:
                # Skip items with override tag (user explicitly approved)
                if self.override_tag in tags:
                    stats['skipped_override'] += 1
                    logger.debug(f"[OTT-MIGRATE] Skipping {title} (has override tag)")
                    continue
                
                # Check if already has provider tags
                res = self.client.get("tag")
                if res:
                    all_tags = {tag["id"]: tag["label"] for tag in res.json()}
                    has_provider_tag = any(
                        tag_id in tags and all_tags.get(tag_id, "").startswith("ott-") 
                        and all_tags.get(tag_id, "") not in ["ott-skipped", "ott-processed", "ott-override"]
                        for tag_id in tags
                    )
                    
                    if has_provider_tag:
                        stats['already_tagged'] += 1
                        logger.debug(f"[OTT-MIGRATE] {title} already has provider tags")
                        continue
                
                # Look up OTT availability
                tmdb_id = item.get("tmdbId")
                imdb_id = item.get("imdbId")
                
                providers = self.justwatch.get_providers(
                    title,
                    year,
                    self.ott_providers,
                    tmdb_id=tmdb_id,
                    imdb_id=imdb_id
                )
                
                # Skip if lookup failed
                if providers is None:
                    logger.warning(f"[OTT-MIGRATE] JustWatch lookup failed for {title}")
                    stats['errors'] += 1
                    continue
                
                # Add provider tags if found
                if providers:
                    logger.info(f"[OTT-MIGRATE] {title} found on: {', '.join(providers)}")
                    
                    # Add provider tags
                    if not self._add_provider_tags(item_id, providers):
                        stats['errors'] += 1
                        continue
                    
                    # Add ott-skipped and ott-processed tags
                    res = self.client.get(f"{self.item_type()}/{item_id}")
                    if res:
                        data = res.json()
                        current_tags = set(data.get("tags", []))
                        current_tags.add(self.skipped_tag)
                        current_tags.add(self.processed_tag)
                        data["tags"] = list(current_tags)
                        
                        # Optionally unmonitor items
                        if unmonitor and data.get("monitored", False):
                            data["monitored"] = False
                            logger.info(f"[OTT-MIGRATE] Unmonitoring {title}")
                        
                        update_res = self.client.put(f"{self.item_type()}/{item_id}", json=data)
                        if update_res:
                            stats['tagged'] += 1
                            logger.debug(f"[OTT-MIGRATE] Added ott-skipped and ott-processed tags to {title}")
                        else:
                            stats['errors'] += 1
                    else:
                        stats['errors'] += 1
                else:
                    stats['not_on_ott'] += 1
                    logger.debug(f"[OTT-MIGRATE] {title} not on OTT")
                
            except Exception as e:
                logger.error(f"[OTT-MIGRATE] Error processing {title}: {e}", exc_info=True)
                stats['errors'] += 1
        
        logger.info(f"[OTT-MIGRATE] Migration complete: {stats}")
        return stats

    @abstractmethod
    def fetch_items(self) -> list[dict[str, Any]]:
        """Fetch all items from *arr service
        
        Returns:
            List of item dictionaries
        """
        ...
    
    @abstractmethod
    def item_type(self) -> str:
        """Get item type name for API endpoints
        
        Returns:
            "movie" for Radarr, "series" for Sonarr, etc.
        """
        ...
