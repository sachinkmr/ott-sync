"""Sonarr manager for series governance"""

import logging
from typing import Any, Optional

from .base import OTTBaseManager

logger = logging.getLogger("ott-hooks")


class SonarrManager(OTTBaseManager):
    """OTT manager for Sonarr (TV series)"""
    
    def __init__(self, *args, anime_detector=None, anime_config: Optional[dict] = None, **kwargs):
        """Initialize Sonarr manager with optional anime detection
        
        Args:
            anime_detector: Optional AnimeDetector instance
            anime_config: Optional anime detection configuration
            *args, **kwargs: Arguments for OTTBaseManager
        """
        super().__init__(*args, **kwargs)
        self.anime_detector = anime_detector
        self.anime_config = anime_config or {}
        self.anime_enabled = anime_detector is not None
    
    def fetch_items(self) -> list[dict[str, Any]]:
        """Fetch all series from Sonarr
        
        Returns:
            List of series dictionaries
        """
        res = self.client.get("series")
        if not res:
            return []
        return res.json()
    
    def item_type(self) -> str:
        """Get item type for Sonarr

        Returns:
            "series"
        """
        return "series"

    def _episode_settled(self, episode: dict, threshold: int) -> bool:
        """True when an episode has a file at or above the resolution threshold."""
        return bool(episode.get("hasFile")) and (
            self._file_resolution(episode.get("episodeFile")) >= threshold
        )

    def _season_complete(
        self, episodes: list[dict], season_number: int, threshold: int,
    ) -> bool:
        """True when every episode of the given season is settled (>= threshold)."""
        season_eps = [e for e in episodes if e.get("seasonNumber") == season_number]
        return bool(season_eps) and all(
            self._episode_settled(e, threshold) for e in season_eps
        )

    def _series_fully_downloaded(self, episodes: list[dict], threshold: int) -> bool:
        """True when every non-special (seasonNumber > 0) episode is settled.

        Specials (season 0) are excluded so unobtained specials never block a
        series-level unmonitor.
        """
        main_eps = [e for e in episodes if e.get("seasonNumber", 0) > 0]
        return bool(main_eps) and all(
            self._episode_settled(e, threshold) for e in main_eps
        )

    def _handle_download_complete(
        self, payload: dict, item: dict, item_id: int, title: str,
    ) -> None:
        """Unmonitor imported episodes, rolling up to season then series.

        - Episode: unmonitor each imported episode whose file is >= threshold.
        - Season: when every episode of an affected season is settled, unmonitor
          that whole season (its episodes + the season's monitored flag).
        - Series: when every non-special episode is settled AND the series has
          ended, unmonitor every non-special episode + season flag + the series.

        Idempotent: only currently-monitored episodes are sent to
        episode/monitor, and the series is PUT only when a season/series flag
        actually changes. At most one episode/monitor PUT and one series PUT.
        """
        threshold = self.unmonitor_on_download_min_resolution
        imported_eps = payload.get("episodes", []) or []
        imported_resolution = self._file_resolution(payload.get("episodeFile"))

        ep_res = self.client.get(
            "episode",
            params={"seriesId": item_id, "includeEpisodeFile": "true"},
        )
        series_res = self.client.get(f"series/{item_id}")
        if not ep_res or not series_res:
            logger.error(f"[UNMONITOR] Failed to fetch state for series id={item_id}")
            return

        episodes = ep_res.json()
        series = series_res.json()
        ep_by_id = {e["id"]: e for e in episodes if e.get("id") is not None}

        # Candidate episode ids; filtered to currently-monitored ones at the end
        # so re-processing an already-unmonitored season is a no-op.
        candidates: set[int] = set()
        if imported_resolution >= threshold:
            candidates |= {e["id"] for e in imported_eps if e.get("id")}
        else:
            logger.info(
                f"[UNMONITOR] series id={item_id} '{title}' imported episode at "
                f"{imported_resolution}p < {threshold}p — keeping monitored for upgrade"
            )

        series_dirty = False
        ended = series.get("status") == "ended"

        if ended and self._series_fully_downloaded(episodes, threshold):
            main_eps = [e for e in episodes if e.get("seasonNumber", 0) > 0]
            candidates |= {e["id"] for e in main_eps if e.get("id")}
            for season in series.get("seasons", []):
                if season.get("seasonNumber", 0) > 0 and season.get("monitored"):
                    season["monitored"] = False
                    series_dirty = True
            if series.get("monitored"):
                series["monitored"] = False
                series_dirty = True
            logger.info(
                f"[UNMONITOR] series id={item_id} '{title}' fully downloaded + "
                f"ended — unmonitoring series"
            )
        else:
            affected = {
                e.get("seasonNumber") for e in imported_eps
                if e.get("seasonNumber") is not None
            }
            for season_number in affected:
                if not self._season_complete(episodes, season_number, threshold):
                    continue
                candidates |= {
                    e["id"] for e in episodes
                    if e.get("seasonNumber") == season_number and e.get("id")
                }
                for season in series.get("seasons", []):
                    if (season.get("seasonNumber") == season_number
                            and season.get("monitored")):
                        season["monitored"] = False
                        series_dirty = True
                logger.info(
                    f"[UNMONITOR] series id={item_id} '{title}' season "
                    f"{season_number} complete — unmonitoring season"
                )

        to_unmonitor = sorted(
            eid for eid in candidates if ep_by_id.get(eid, {}).get("monitored")
        )
        if to_unmonitor:
            self.client.put(
                "episode/monitor",
                json={"episodeIds": to_unmonitor, "monitored": False},
            )
        if series_dirty:
            self.client.put(f"series/{item_id}", json=series)

    def _process_webhook_locked(self, payload: dict, event: str, item: dict,
                                  title: str, year: int, item_id: int, tags: set) -> None:
        """Process webhook with item lock held
        
        Overrides base class to add anime detection before OTT processing.
        
        Args:
            payload: Full webhook payload
            event: Event type
            item: Item data
            title: Item title
            year: Item year
            item_id: Item ID
            tags: Item tags
        """
        # ─────────────────────────────────────────────
        # Anime Detection (if enabled)
        # ─────────────────────────────────────────────
        if self.anime_enabled:
            # Re-fetch current item state for anime detection
            res = self.client.get(f"series/{item_id}")
            if res:
                current_item = res.json()
                current_tags = set(current_item.get("tags", []))
                
                # Only run detection if not already checked
                if self.anime_checked_tag not in current_tags:
                    logger.info(f"[ANIME] Running detection for webhook: {title} ({year})")
                    try:
                        classification = self.detect_and_set_anime(item_id, current_item)
                        
                        # Send notification for MAYBE classifications (needs manual review)
                        if classification == "MAYBE_ANIME":
                            notify_maybe = self.anime_config.get("telegram", {}).get("notify_maybe", True)
                            if notify_maybe and self.telegram:
                                self._send_anime_maybe_notification(item_id, title, year, current_item)
                        
                    except Exception as e:
                        logger.error(f"[ANIME] Detection failed for {title}: {e}", exc_info=True)
                else:
                    logger.debug(f"[ANIME] Already checked, skipping: {title}")
        
        # ─────────────────────────────────────────────
        # Continue with normal OTT processing
        # ─────────────────────────────────────────────
        super()._process_webhook_locked(payload, event, item, title, year, item_id, tags)
    
    def _send_anime_maybe_notification(
        self,
        series_id: int,
        title: str,
        year: int,
        series_data: dict
    ) -> None:
        """Send Telegram notification for MAYBE anime requiring review
        
        Args:
            series_id: Sonarr series ID
            title: Series title
            year: Release year
            series_data: Full series data dict
        """
        # Extract poster
        images = series_data.get("images", [])
        poster = next(
            (img.get("remoteUrl") for img in images if img.get("coverType") == "poster"),
            None
        )
        
        # Build notification message
        message = (
            f"🤔 *Possible Anime Detected*\\n\\n"
            f"📺 *{title}*{f' ({year})' if year else ''}\\n\\n"
            f"This series might be anime but needs manual review\\."
        )
        
        # Add confirmation buttons
        buttons = [[
            {"text": "✅ Yes, it's anime", "callback_data": f"anime:confirm:{series_id}"},
            {"text": "❌ Not anime", "callback_data": f"anime:reject:{series_id}"}
        ]]
        
        # Send notification to admin chat
        admin_chat_id = self.anime_config.get("telegram", {}).get("admin_chat_id")
        if admin_chat_id:
            if poster:
                self.telegram.send_photo(poster, message, buttons=buttons, chat_id=admin_chat_id)
            else:
                self.telegram.send(message, buttons=buttons, chat_id=admin_chat_id)
        else:
            # Fallback to main chat if no admin chat configured
            if poster:
                self.telegram.send_photo(poster, message, buttons=buttons)
            else:
                self.telegram.send(message, buttons=buttons)

    # -------------------- Anime Detection --------------------
    
    def detect_and_set_anime(
        self,
        series_id: int,
        series_data: Optional[dict] = None
    ) -> Optional[str]:
        """Detect if series is anime and apply metadata changes
        
        Args:
            series_id: Sonarr series ID
            series_data: Optional series data dict (fetched if not provided)
            
        Returns:
            Classification ("DEFINITE_ANIME", "LIKELY_ANIME", "MAYBE_ANIME", "NOT_ANIME")
            or None if anime detection is disabled
        """
        if not self.anime_enabled:
            logger.debug(f"[ANIME] Detection disabled, skipping series {series_id}")
            return None
        
        # Fetch series data if not provided
        if series_data is None:
            res = self.client.get(f"series/{series_id}")
            if not res:
                logger.error(f"[ANIME] Failed to fetch series {series_id}")
                return None
            series_data = res.json()
        
        title = series_data.get("title", "Unknown")
        year = series_data.get("year")
        tmdb_id = series_data.get("tvdbId")  # Note: Sonarr uses tvdbId, not tmdb
        
        # For now, we'll search by title+year since Sonarr doesn't store TMDB ID directly
        # TODO: Add external ID lookup if needed
        logger.info(f"[ANIME] Detecting: {title} ({year}) [Sonarr ID: {series_id}]")
        
        # Run detection
        classification, reason, signals = self.anime_detector.detect(
            title=title,
            year=year,
            tmdb_id=None  # Will be looked up via TMDB search if needed
        )
        
        logger.info(
            f"[ANIME] Result: {classification} | Score: {signals.get('total_score', 0)}/100 | {reason}"
        )
        
        # Update tags
        self._update_anime_tags(series_id, series_data, classification)
        
        # Apply metadata changes based on classification
        if self.anime_config.get("metadata", {}).get("auto_set_series_type", True):
            if classification in ("DEFINITE_ANIME", "LIKELY_ANIME"):
                # Set as anime
                self.set_anime_metadata(series_id, series_data, is_anime=True)
            else:
                # Set as standard TV
                self.set_anime_metadata(series_id, series_data, is_anime=False)
        
        return classification
    
    def set_anime_metadata(
        self,
        series_id: int,
        series_data: Optional[dict] = None,
        is_anime: bool = True,
        force: bool = False
    ) -> bool:
        """Apply series type and quality profile based on anime classification
        
        Args:
            series_id: Sonarr series ID
            series_data: Optional series data dict (fetched if not provided)
            is_anime: True to set as anime, False to set as standard TV
            force: Override even if already has correct profile
            
        Returns:
            True if successful, False otherwise
        """
        if not self.anime_enabled:
            return False
        
        # Fetch series data if not provided
        if series_data is None:
            res = self.client.get(f"series/{series_id}")
            if not res:
                logger.error(f"[ANIME] Failed to fetch series {series_id}")
                return False
            series_data = res.json()
        
        title = series_data.get("title", "Unknown")
        current_type = series_data.get("seriesType", "standard")
        current_profile_id = series_data.get("qualityProfileId")
        
        # Get current profile name
        profile_res = self.client.get(f"qualityprofile/{current_profile_id}")
        current_profile_name = ""
        if profile_res:
            current_profile_name = profile_res.json().get("name", "")
        
        # Determine target profile and series type
        if is_anime:
            target_type = "anime"
            target_profile_name = self.anime_config.get("metadata", {}).get("profile_name", "Anime")
            target_profile_id = self.get_anime_profile_id()
            
            if not target_profile_id:
                logger.warning(f"[ANIME] No anime profile found, skipping metadata update for {title}")
                return False
            
            # Skip if already has anime profile (unless forced)
            skip_if_has_anime = self.anime_config.get("metadata", {}).get(
                "skip_if_profile_contains_anime", True
            )
            if not force and skip_if_has_anime and "anime" in current_profile_name.lower():
                logger.info(
                    f"[ANIME] Skipping {title}: already has anime profile '{current_profile_name}'"
                )
                return True
        else:
            target_type = "standard"
            target_profile_name = "TV"  # or could be configurable
            target_profile_id = self.get_tv_profile_id()
            
            if not target_profile_id:
                logger.warning(f"[ANIME] No TV profile found, skipping metadata update for {title}")
                return False
        
        # Prepare updates
        updates = {}
        
        # Set series type
        if self.anime_config.get("metadata", {}).get("auto_set_series_type", True):
            if current_type != target_type:
                updates["seriesType"] = target_type
                logger.info(f"[ANIME] Setting series type: {current_type} → {target_type}")
        
        # Set profile
        if self.anime_config.get("metadata", {}).get("auto_set_profile", True):
            if current_profile_id != target_profile_id:
                updates["qualityProfileId"] = target_profile_id
                logger.info(
                    f"[ANIME] Setting profile: {current_profile_name} → {target_profile_name} (ID: {target_profile_id})"
                )
        
        # Apply updates if any
        if updates:
            series_data.update(updates)
            res = self.client.put(f"series/{series_id}", json=series_data)
            if not res:
                logger.error(f"[ANIME] Failed to update series {series_id}")
                return False
            logger.info(f"[ANIME] ✓ Updated {title}")
            return True
        else:
            logger.info(f"[ANIME] No changes needed for {title}")
            return True
    
    def get_anime_profile_id(self) -> Optional[int]:
        """Get anime quality profile ID from Sonarr
        
        Returns:
            Profile ID or None if not found
        """
        profile_name = self.anime_config.get("metadata", {}).get("profile_name", "Anime")
        
        res = self.client.get("qualityprofile")
        if not res:
            logger.error("[ANIME] Failed to fetch quality profiles")
            return None
        
        profiles = res.json()
        for profile in profiles:
            if profile["name"].lower() == profile_name.lower():
                return profile["id"]
        
        logger.warning(f"[ANIME] Quality profile '{profile_name}' not found")
        return None
    
    def get_tv_profile_id(self) -> Optional[int]:
        """Get TV quality profile ID from Sonarr
        
        Returns:
            Profile ID or None if not found
        """
        # Try to find profile named "TV" (case-insensitive)
        res = self.client.get("qualityprofile")
        if not res:
            logger.error("[ANIME] Failed to fetch quality profiles")
            return None
        
        profiles = res.json()
        
        # First try exact match for "TV"
        for profile in profiles:
            if profile["name"].lower() == "tv":
                return profile["id"]
        
        # Fallback: find first profile that doesn't contain "anime"
        for profile in profiles:
            if "anime" not in profile["name"].lower():
                logger.info(f"[ANIME] Using '{profile['name']}' as TV profile (fallback)")
                return profile["id"]
        
        logger.warning("[ANIME] No suitable TV profile found")
        return None
    
    def _update_anime_tags(
        self,
        series_id: int,
        series_data: dict,
        classification: str
    ) -> None:
        """Update anime detection tags for a series
        
        Args:
            series_id: Sonarr series ID
            series_data: Series data dict
            classification: Detection result
        """
        current_tags = set(series_data.get("tags", []))
        title = series_data.get("title", "Unknown")
        
        # Always add checked tag
        current_tags.add(self.anime_checked_tag)
        
        # Remove old detection tags
        current_tags.discard(self.anime_detected_tag)
        current_tags.discard(self.anime_maybe_tag)
        
        # Add appropriate detection tag
        if classification in ("DEFINITE_ANIME", "LIKELY_ANIME"):
            current_tags.add(self.anime_detected_tag)
            logger.info(f"[ANIME] Tagged {title} as anime-detected")
        elif classification == "MAYBE_ANIME":
            current_tags.add(self.anime_maybe_tag)
            logger.info(f"[ANIME] Tagged {title} as anime-maybe (needs review)")
        
        # Update series tags
        series_data["tags"] = list(current_tags)
        res = self.client.put(f"series/{series_id}", json=series_data)
        if not res:
            logger.error(f"[ANIME] Failed to update tags for series {series_id}")
    
    def migrate_all_anime(self, page_size: int = 50) -> dict[str, Any]:
        """Process all series for anime detection
        
        Args:
            page_size: Number of series to process in each batch
            
        Returns:
            Migration statistics dict
        """
        if not self.anime_enabled:
            logger.warning("[ANIME] Migration skipped: anime detection disabled")
            return {"error": "Anime detection disabled"}
        
        logger.info("[ANIME] Starting anime migration for all series")
        
        stats = {
            "total": 0,
            "checked": 0,
            "definite": 0,
            "likely": 0,
            "maybe": 0,
            "not_anime": 0,
            "errors": 0
        }
        
        # Fetch all series
        series_list = self.fetch_items()
        stats["total"] = len(series_list)
        
        logger.info(f"[ANIME] Found {stats['total']} series to process")
        
        # Process in batches
        for i in range(0, len(series_list), page_size):
            batch = series_list[i:i + page_size]
            batch_num = (i // page_size) + 1
            total_batches = (len(series_list) + page_size - 1) // page_size
            
            logger.info(f"[ANIME] Processing batch {batch_num}/{total_batches}")
            
            for series in batch:
                series_id = series.get("id")
                title = series.get("title", "Unknown")
                current_tags = set(series.get("tags", []))
                
                # Skip if already checked (honor anime-checked tag)
                if self.anime_checked_tag in current_tags:
                    logger.debug(f"[ANIME] Already checked, skipping: {title}")
                    continue
                
                try:
                    classification = self.detect_and_set_anime(series_id, series)
                    
                    if classification:
                        stats["checked"] += 1
                        
                        if classification == "DEFINITE_ANIME":
                            stats["definite"] += 1
                        elif classification == "LIKELY_ANIME":
                            stats["likely"] += 1
                        elif classification == "MAYBE_ANIME":
                            stats["maybe"] += 1
                        else:
                            stats["not_anime"] += 1
                
                except Exception as e:
                    logger.error(f"[ANIME] Error processing {title}: {e}")
                    stats["errors"] += 1
        
        logger.info(
            f"[ANIME] Migration complete | "
            f"Total: {stats['total']} | "
            f"Checked: {stats['checked']} | "
            f"Definite: {stats['definite']} | "
            f"Likely: {stats['likely']} | "
            f"Maybe: {stats['maybe']} | "
            f"Not Anime: {stats['not_anime']} | "
            f"Errors: {stats['errors']}"
        )
        
        return stats
