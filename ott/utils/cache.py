"""JustWatch cache layer using SQLite database"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from ..db.client import get_db
from ..db.schema import JustWatchCacheModel

logger = logging.getLogger("ott-hooks")


class JustWatchCache:
    """Database-backed cache for JustWatch provider lookups
    
    Features:
    - TTL-based expiration (7 days for found, 24 hours for not found)
    - Deduplication by TMDB ID, IMDB ID, or title+year
    - Cache hit/miss metrics
    - Automatic cleanup of expired entries
    """
    
    def __init__(
        self,
        ttl_found_days: int = 7,
        ttl_not_found_hours: int = 24,
        max_size_mb: int = 50,
    ):
        """Initialize JustWatch cache
        
        Args:
            ttl_found_days: TTL in days for found OTT providers (default: 7)
            ttl_not_found_hours: TTL in hours for not found results (default: 24)
            max_size_mb: Maximum cache size in MB (default: 50)
        """
        self.ttl_found_days = ttl_found_days
        self.ttl_not_found_hours = ttl_not_found_hours
        self.max_size_mb = max_size_mb
        
        self._cache_hits = 0
        self._cache_misses = 0
        
        logger.info(f"[CACHE] JustWatch cache initialized")
        logger.info(f"[CACHE] TTL: {ttl_found_days}d (found), {ttl_not_found_hours}h (not found)")
        logger.info(f"[CACHE] Max size: {max_size_mb}MB")
    
    def _generate_cache_key(
        self,
        title: str,
        year: Optional[int],
        region: str,
        tmdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> str:
        """Generate unique cache key
        
        Args:
            title: Movie/series title
            year: Release year
            region: Region code
            tmdb_id: TMDB ID (preferred)
            imdb_id: IMDB ID (fallback)
            
        Returns:
            SHA256 hash of cache key components
        """
        # Prefer TMDB ID, then IMDB ID, then title+year
        if tmdb_id:
            key_parts = [str(tmdb_id), region]
        elif imdb_id:
            key_parts = [imdb_id, region]
        else:
            key_parts = [title.lower(), str(year or ""), region]
        
        key_string = "|".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()
    
    def get(
        self,
        title: str,
        year: Optional[int],
        region: str,
        item_type: str,
        tmdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> Optional[list[str]]:
        """Get cached JustWatch providers
        
        Args:
            title: Movie/series title
            year: Release year
            region: Region code
            item_type: 'movie' or 'series'
            tmdb_id: TMDB ID (optional)
            imdb_id: IMDB ID (optional)
            
        Returns:
            List of provider names if cached and not expired, None otherwise
        """
        try:
            db = get_db()
            
            with db.session() as session:
                # Try exact match by TMDB ID
                if tmdb_id:
                    cache_entry = session.query(JustWatchCacheModel).filter(
                        JustWatchCacheModel.tmdb_id == tmdb_id,
                        JustWatchCacheModel.region == region,
                        JustWatchCacheModel.expires_at > datetime.utcnow(),
                    ).first()
                    
                    if cache_entry:
                        self._handle_cache_hit(session, cache_entry)
                        providers = json.loads(cache_entry.providers_json) if cache_entry.providers_json else []
                        logger.debug(f"[CACHE] HIT (TMDB {tmdb_id}): {len(providers)} providers")
                        return providers  # [] means "cached as not-on-OTT"
                
                # Try exact match by IMDB ID
                if imdb_id:
                    cache_entry = session.query(JustWatchCacheModel).filter(
                        JustWatchCacheModel.imdb_id == imdb_id,
                        JustWatchCacheModel.region == region,
                        JustWatchCacheModel.expires_at > datetime.utcnow(),
                    ).first()
                    
                    if cache_entry:
                        self._handle_cache_hit(session, cache_entry)
                        providers = json.loads(cache_entry.providers_json) if cache_entry.providers_json else []
                        logger.debug(f"[CACHE] HIT (IMDB {imdb_id}): {len(providers)} providers")
                        return providers  # [] means "cached as not-on-OTT"
                
                # Fallback: try title + year match
                cache_entry = session.query(JustWatchCacheModel).filter(
                    JustWatchCacheModel.title == title,
                    JustWatchCacheModel.year == year,
                    JustWatchCacheModel.region == region,
                    JustWatchCacheModel.item_type == item_type,
                    JustWatchCacheModel.expires_at > datetime.utcnow(),
                ).first()
                
                if cache_entry:
                    self._handle_cache_hit(session, cache_entry)
                    providers = json.loads(cache_entry.providers_json) if cache_entry.providers_json else []
                    logger.debug(f"[CACHE] HIT (title): {title} ({year}) - {len(providers)} providers")
                    return providers if providers else None
            
            # Cache miss
            self._cache_misses += 1
            logger.debug(f"[CACHE] MISS: {title} ({year})")
            return None
            
        except Exception as e:
            logger.error(f"[CACHE] Error reading cache: {e}", exc_info=True)
            return None
    
    def _handle_cache_hit(self, session, cache_entry: JustWatchCacheModel) -> None:
        """Update cache hit statistics
        
        Args:
            session: Database session
            cache_entry: Cache entry that was hit
        """
        self._cache_hits += 1
        cache_entry.cache_hit_count += 1
        cache_entry.last_accessed = datetime.utcnow()
        session.commit()
    
    def set(
        self,
        title: str,
        year: Optional[int],
        region: str,
        item_type: str,
        providers: Optional[list[str]],
        tmdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> None:
        """Store JustWatch providers in cache
        
        Args:
            title: Movie/series title
            year: Release year
            region: Region code
            item_type: 'movie' or 'series'
            providers: List of provider names (or None if not found)
            tmdb_id: TMDB ID (optional)
            imdb_id: IMDB ID (optional)
        """
        try:
            db = get_db()
            
            # Calculate expiration based on whether providers were found
            found_on_ott = bool(providers)
            if found_on_ott:
                expires_at = datetime.utcnow() + timedelta(days=self.ttl_found_days)
            else:
                expires_at = datetime.utcnow() + timedelta(hours=self.ttl_not_found_hours)
            
            with db.session() as session:
                # Check if entry already exists
                existing = None
                
                if tmdb_id:
                    existing = session.query(JustWatchCacheModel).filter(
                        JustWatchCacheModel.tmdb_id == tmdb_id,
                        JustWatchCacheModel.region == region,
                    ).first()
                elif imdb_id:
                    existing = session.query(JustWatchCacheModel).filter(
                        JustWatchCacheModel.imdb_id == imdb_id,
                        JustWatchCacheModel.region == region,
                    ).first()
                else:
                    existing = session.query(JustWatchCacheModel).filter(
                        JustWatchCacheModel.title == title,
                        JustWatchCacheModel.year == year,
                        JustWatchCacheModel.region == region,
                        JustWatchCacheModel.item_type == item_type,
                    ).first()
                
                if existing:
                    # Update existing entry
                    existing.providers_json = json.dumps(providers) if providers else None
                    existing.found_on_ott = found_on_ott
                    existing.cached_at = datetime.utcnow()
                    existing.expires_at = expires_at
                    existing.last_accessed = datetime.utcnow()
                    logger.debug(f"[CACHE] UPDATED: {title} ({year})")
                else:
                    # Create new entry
                    cache_entry = JustWatchCacheModel(
                        tmdb_id=tmdb_id,
                        imdb_id=imdb_id,
                        title=title,
                        year=year,
                        region=region,
                        item_type=item_type,
                        providers_json=json.dumps(providers) if providers else None,
                        found_on_ott=found_on_ott,
                        cached_at=datetime.utcnow(),
                        expires_at=expires_at,
                        last_accessed=datetime.utcnow(),
                        cache_hit_count=0,
                    )
                    session.add(cache_entry)
                    logger.debug(f"[CACHE] STORED: {title} ({year}) - expires {expires_at}")
                
                session.commit()
            
        except Exception as e:
            logger.error(f"[CACHE] Error writing cache: {e}", exc_info=True)
    
    def invalidate(
        self,
        tmdb_id: Optional[int] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
    ) -> int:
        """Invalidate cache entries
        
        Args:
            tmdb_id: TMDB ID to invalidate (optional)
            title: Title to invalidate (optional)
            year: Year to invalidate (optional)
            
        Returns:
            Number of entries deleted
        """
        try:
            db = get_db()
            
            with db.session() as session:
                query = session.query(JustWatchCacheModel)
                
                if tmdb_id:
                    query = query.filter(JustWatchCacheModel.tmdb_id == tmdb_id)
                elif title:
                    query = query.filter(JustWatchCacheModel.title == title)
                    if year:
                        query = query.filter(JustWatchCacheModel.year == year)
                else:
                    # No filters provided - delete all (dangerous!)
                    logger.warning("[CACHE] Invalidating entire cache!")
                
                count = query.delete()
                session.commit()
                
                logger.info(f"[CACHE] Invalidated {count} entries")
                return count
                
        except Exception as e:
            logger.error(f"[CACHE] Error invalidating cache: {e}", exc_info=True)
            return 0
    
    def cleanup_expired(self) -> int:
        """Remove expired cache entries
        
        Returns:
            Number of entries deleted
        """
        try:
            db = get_db()
            
            with db.session() as session:
                deleted = session.query(JustWatchCacheModel).filter(
                    JustWatchCacheModel.expires_at < datetime.utcnow()
                ).delete()
                
                session.commit()
                
                if deleted > 0:
                    logger.info(f"[CACHE] Cleaned up {deleted} expired entries")
                
                return deleted
                
        except Exception as e:
            logger.error(f"[CACHE] Error cleaning up cache: {e}", exc_info=True)
            return 0
    
    def get_stats(self) -> dict:
        """Get cache statistics
        
        Returns:
            Dictionary with cache statistics
        """
        try:
            db = get_db()
            
            with db.session() as session:
                total_entries = session.query(JustWatchCacheModel).count()
                expired_entries = session.query(JustWatchCacheModel).filter(
                    JustWatchCacheModel.expires_at < datetime.utcnow()
                ).count()
                
                hit_rate = 0.0
                if self._cache_hits + self._cache_misses > 0:
                    hit_rate = round(self._cache_hits / (self._cache_hits + self._cache_misses) * 100, 2)
                
                return {
                    "total_entries": total_entries,
                    "expired_entries": expired_entries,
                    "active_entries": total_entries - expired_entries,
                    "cache_hits": self._cache_hits,
                    "cache_misses": self._cache_misses,
                    "hit_rate_percent": hit_rate,
                }
                
        except Exception as e:
            logger.error(f"[CACHE] Error getting stats: {e}", exc_info=True)
            return {}
