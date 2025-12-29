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
        timestamp_cache: TimestampCache | None = None
    ):
        """Initialize OTT manager
        
        Args:
            arr_client: HTTP client for Radarr/Sonarr API
            justwatch_client: Client for OTT provider lookup
            telegram: Telegram notification client
            ott_providers: Set of allowed OTT provider names
            timestamp_cache: Optional timestamp cache for periodic re-checks
        """
        self.client = arr_client
        self.justwatch = justwatch_client
        self.telegram = telegram
        self.ott_providers = ott_providers
        self.timestamp_cache = timestamp_cache or TimestampCache()
        self.pool = ThreadPoolExecutor(max_workers=DEFAULT_THREAD_POOL_SIZE)
        self._metrics_lock = threading.Lock()
    
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

        # Cancel pending downloads
        self.client.post("command", json={
            "name": "CancelPendingDownloads",
            f"{self.item_type()}Ids": [item_id],
        })

        # Remove from download queue
        self.client.delete("queue", params={
            f"{self.item_type()}Id": item_id,
            "removeFromClient": True,
        })

        # Get current item state
        res = self.client.get(f"{self.item_type()}/{item_id}")
        if not res:
            return

        # Update item: unmonitor and tag
        data = res.json()
        data["monitored"] = False
        data["tags"] = list(
            set(data.get("tags", [])) | {self.skipped_tag, self.processed_tag}
        )

        self.client.put(f"{self.item_type()}/{item_id}", json=data)
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
        event = payload.get("eventType")
        item = payload.get(self.item_type(), {})
    
        title = item.get("title")
        year = item.get("year")
        item_id = item.get("id")
        tags = set(item.get("tags", []))
    
        logger.info(f"[WEBHOOK] {event} → {title} ({year}) id={item_id}")
    
        if event not in ("MovieAdded", "SeriesAdded", "Grab"):
            return
    
        # 🔒 Absolute override guard
        if self.override_tag in tags:
            logger.info(
                f"[DECISION] ott-override present → skip ALL OTT enforcement "
                f"for {self.item_type()} id={item_id}"
            )
            return
    
        if event == "Grab":
            logger.info("[DECISION] Grab event → checking OTT as fallback")
    
        # ─────────────────────────────────────────────
        # OTT lookup decision (JustWatch only)
        # ─────────────────────────────────────────────
        providers = None
        was_previously_blocked = False
    
        if self.processed_tag in tags:
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
            # 🖼 Extract poster if available
            images = item.get("images", [])
            poster = next(
                (img.get("remoteUrl") for img in images if img.get("coverType") == "poster"),
                None,
            )
    
            # 👤 Best-effort requester info
            requested_by = (
                payload.get("username")
                or payload.get("requestedBy")
                or payload.get("author")
                or "Overseerr"
            )
    
            caption = build_telegram_caption(
                title=title,
                year=year,
                provider=providers[0],
                region=self.telegram.region,
                item_type=self.item_type(),
                item_id=item_id,
                requested_by=requested_by,
            )
    
            buttons = [[{
                "text": "⬇️ Download anyway",
                "callback_data": f"override:{self.item_type()}:{item_id}"
            }]]
    
            # 📣 Best-effort Telegram notification (doesn't affect blocking decision)
            notification_success = False
            if was_previously_blocked:
                # Re-blocking scenario - simpler message
                notification_success = self.telegram.send(
                    f"🚨 *Re-block detected*\n\n"
                    f"🎬 *{title}*{f' ({year})' if year else ''}\n\n"
                    f"This item was previously blocked but a download was attempted.\n"
                    f"Use the button below to approve if this was intentional.",
                    buttons=buttons
                )
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

            # 🚫 Enforce block (delete files only if NOT previously blocked)
            self.enforce_block(item_id, delete_files=not was_previously_blocked)

            # 🕒 Update timestamp for periodic re-check
            self.timestamp_cache.update_check(self.item_type(), item_id)

            # ✅ Mark processed AFTER a successful decision
            if self.processed_tag not in tags:
                res = self.client.get(f"{self.item_type()}/{item_id}")
                if res:
                    data = res.json()
                    data["tags"] = list(set(data.get("tags", [])) | {self.processed_tag})
                    self.client.put(f"{self.item_type()}/{item_id}", json=data)
        else:
            # ❌ Not on OTT → mark processed to avoid future lookups
            logger.info("[DECISION] Not on OTT → marking processed")
            
            # 🕒 Update timestamp even for not-found items
            self.timestamp_cache.update_check(self.item_type(), item_id)
    
            if self.processed_tag not in tags:
                res = self.client.get(f"{self.item_type()}/{item_id}")
                if res:
                    data = res.json()
                    data["tags"] = list(set(data.get("tags", [])) | {self.processed_tag})
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
            tags = set(item.get("tags", []))
            item_id = item.get("id")
            
            # 🔒 Respect override - never touch items with ott-override
            if self.override_tag in tags:
                metrics.skipped_override += 1
                continue
                
            # Skip unmonitored items
            if not item.get("monitored"):
                continue
            
            # 🔄 Check processed_tag BEFORE JustWatch to avoid collision with webhook
            # BUT allow re-check if item hasn't been checked in 30+ days (OTT availability changes)
            if self.processed_tag in tags and self.skipped_tag not in tags:
                # Check if we should re-check this item (30-day period)
                if not self.timestamp_cache.should_recheck(self.item_type(), item_id, recheck_days=30):
                    metrics.already_processed += 1
                    continue
                else:
                    logger.info(f"[CRON] Re-checking {item_id} (30+ days since last check)")
            
            # 🕵️ Manual unmonitor bypass detection - re-enforce block if monitored with ott-skipped
            if item.get("monitored") and self.skipped_tag in tags:
                logger.warning(f"[CRON] Manual unmonitor bypass detected for {item_id}")
                metrics.cleaned += 1
                self.enforce_block(item_id, delete_files=False)  # Re-block without deleting files
                self.timestamp_cache.update_check(self.item_type(), item_id)
                continue

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
                continue   # Skip this item, continue with others
            
            if providers:
                metrics.cleaned += 1
                # 🗑️ Cron also deletes files (saves disk space)
                self.enforce_block(item_id, delete_files=True)
                self.timestamp_cache.update_check(self.item_type(), item_id)
            else:
                metrics.marked_processed += 1
                res = self.client.get(f"{self.item_type()}/{item_id}")
                if res:
                    data = res.json()
                    data["tags"] = list(
                        set(data.get("tags", [])) | {self.processed_tag}
                    )
                    self.client.put(f"{self.item_type()}/{item_id}", json=data)
                    self.timestamp_cache.update_check(self.item_type(), item_id)

        logger.info(f"[CRON] Completed → {metrics}")
        return metrics

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
