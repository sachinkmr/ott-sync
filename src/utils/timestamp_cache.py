"""Timestamp cache for tracking OTT re-check periods"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ott-hooks")


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
        
        try:
            return datetime.fromisoformat(timestamp_str)
        except ValueError:
            logger.warning(f"[CACHE] Invalid timestamp for {key}: {timestamp_str}")
            return None
    
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
        
        threshold = datetime.now() - timedelta(days=recheck_days)
        return last_check < threshold
    
    def update_check(self, item_type: str, item_id: int) -> None:
        """Update last check timestamp for item
        
        Args:
            item_type: "movie" or "series"
            item_id: Item ID
        """
        key = self._make_key(item_type, item_id)
        self.cache[key] = datetime.now().isoformat()
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
