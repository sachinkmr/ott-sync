"""Timestamp cache for tracking OTT re-check periods"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ott-hooks")


def _utcnow() -> datetime:
    """Timezone-aware UTC 'now' - matches the DB/cache layers."""
    return datetime.now(timezone.utc)


def _parse_timestamp(raw: str) -> Optional[datetime]:
    """Parse an ISO timestamp, normalizing naive values to UTC.

    Older cache files stored naive local-time timestamps. Treat those as UTC
    (close enough for 30-day recheck windows) so we can compare against
    timezone-aware values without crashing.
    """
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class TimestampCache:
    """Simple file-based timestamp cache for OTT re-check tracking
    
    Stores when each item was last checked for OTT availability.
    Used to determine when to re-check items (e.g., every 30 days).
    """
    
    def __init__(self, cache_file: str = "ott_timestamps.json"):
        """Initialize timestamp cache
        
        Args:
            cache_file: Path to JSON cache file
        """
        self.cache_file = Path(cache_file)
        self.cache: dict[str, str] = {}  # {item_key: iso_timestamp}
        self._load()
    
    def _load(self) -> None:
        """Load cache from file"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    self.cache = json.load(f)
                logger.info(f"[CACHE] Loaded {len(self.cache)} timestamps from {self.cache_file}")
            except Exception as e:
                logger.error(f"[CACHE] Failed to load {self.cache_file}: {e}")
                self.cache = {}
        else:
            logger.info(f"[CACHE] No cache file found at {self.cache_file}, starting fresh")
    
    def _save(self) -> None:
        """Save cache to file"""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.error(f"[CACHE] Failed to save {self.cache_file}: {e}")
    
    def _make_key(self, item_type: str, item_id: int) -> str:
        """Generate cache key
        
        Args:
            item_type: "movie" or "series"
            item_id: Item ID from Radarr/Sonarr
            
        Returns:
            Cache key string
        """
        return f"{item_type}:{item_id}"
    
    def get_last_check(self, item_type: str, item_id: int) -> Optional[datetime]:
        """Get last OTT check timestamp for item
        
        Args:
            item_type: "movie" or "series"
            item_id: Item ID
            
        Returns:
            datetime of last check, or None if never checked
        """
        key = self._make_key(item_type, item_id)
        timestamp_str = self.cache.get(key)

        if not timestamp_str:
            return None

        parsed = _parse_timestamp(timestamp_str)
        if parsed is None:
            logger.warning(f"[CACHE] Invalid timestamp for {key}: {timestamp_str}")
        return parsed
    
    def should_recheck(
        self, 
        item_type: str, 
        item_id: int, 
        recheck_days: int = 30
    ) -> bool:
        """Check if item should be re-checked for OTT availability
        
        Args:
            item_type: "movie" or "series"
            item_id: Item ID
            recheck_days: Number of days between re-checks (default 30)
            
        Returns:
            True if item should be re-checked
        """
        last_check = self.get_last_check(item_type, item_id)
        
        if last_check is None:
            return True  # Never checked
        
        threshold = _utcnow() - timedelta(days=recheck_days)
        return last_check < threshold
    
    def update_check(self, item_type: str, item_id: int) -> None:
        """Update last check timestamp for item
        
        Args:
            item_type: "movie" or "series"
            item_id: Item ID
        """
        key = self._make_key(item_type, item_id)
        self.cache[key] = _utcnow().isoformat()
        self._save()
    
    def clear_item(self, item_type: str, item_id: int) -> None:
        """Clear timestamp for specific item
        
        Args:
            item_type: "movie" or "series"
            item_id: Item ID
        """
        key = self._make_key(item_type, item_id)
        if key in self.cache:
            del self.cache[key]
            self._save()
            logger.info(f"[CACHE] Cleared timestamp for {key}")
