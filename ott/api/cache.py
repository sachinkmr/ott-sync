"""Cache management API endpoints"""

import logging
from typing import Optional
from fastapi import Request, HTTPException
from pydantic import BaseModel

from .app import app
from ..utils.cache import JustWatchCache
from ..db.client import get_db

logger = logging.getLogger("ott-hooks")

# Global cache instance (set in main.py)
_cache: Optional[JustWatchCache] = None


def register_cache_routes(get_cache: callable):
    """Register cache management endpoints
    
    Args:
        get_cache: Callable that returns JustWatchCache instance
    """
    
    @app.get(
        "/cache/stats",
        summary="Get cache statistics",
        description="Returns cache hit/miss rates, size, and entry counts",
        tags=["Cache"],
    )
    async def get_cache_stats():
        """Get JustWatch cache statistics
        
        Returns:
            Cache statistics including hit rate, size, and entry counts
            
        Example response:
        ```json
        {
          "total_entries": 1500,
          "active_entries": 1200,
          "expired_entries": 300,
          "cache_hits": 5000,
          "cache_misses": 1200,
          "hit_rate_percent": 80.65
        }
        ```
        """
        try:
            cache = get_cache()
            stats = cache.get_stats()
            return stats
        except Exception as e:
            logger.error(f"[CACHE] Failed to get stats: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post(
        "/cache/invalidate",
        summary="Invalidate cache entries",
        description="Remove specific cache entries or clear entire cache (localhost only)",
        tags=["Cache"],
    )
    async def invalidate_cache(
        request: Request,
        tmdb_id: Optional[int] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
    ):
        """Invalidate JustWatch cache entries
        
        **Security**: This endpoint is restricted to localhost and local network only.
        
        Args:
            tmdb_id: TMDB ID to invalidate (optional)
            title: Title to invalidate (optional)
            year: Year to invalidate along with title (optional)
            
        Returns:
            Number of entries deleted
            
        Examples:
        ```bash
        # Invalidate specific movie by TMDB ID
        curl -X POST "http://localhost:9123/cache/invalidate?tmdb_id=550"
        
        # Invalidate by title
        curl -X POST "http://localhost:9123/cache/invalidate?title=Inception&year=2010"
        
        # Clear entire cache (use with caution!)
        curl -X POST "http://localhost:9123/cache/invalidate"
        ```
        """
        # Security check - only allow from localhost and local network
        client_ip = request.client.host
        allowed_ips = ["127.0.0.1", "localhost", "::1"]
        is_local_network = client_ip.startswith("192.168.") or client_ip.startswith("10.") or client_ip.startswith("172.")
        
        if client_ip not in allowed_ips and not is_local_network:
            logger.warning(f"[CACHE] Cache invalidation blocked from {client_ip}")
            raise HTTPException(
                status_code=403,
                detail="Cache invalidation is restricted to localhost and local network only"
            )
        
        try:
            cache = get_cache()
            deleted = cache.invalidate(tmdb_id=tmdb_id, title=title, year=year)
            
            logger.info(f"[CACHE] Invalidated {deleted} entries (from {client_ip})")
            
            return {
                "ok": True,
                "deleted": deleted,
                "message": f"Invalidated {deleted} cache entries"
            }
        except Exception as e:
            logger.error(f"[CACHE] Failed to invalidate cache: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post(
        "/cache/cleanup",
        summary="Clean up expired cache entries",
        description="Remove expired cache entries to reclaim space (localhost only)",
        tags=["Cache"],
    )
    async def cleanup_cache(request: Request):
        """Clean up expired cache entries
        
        **Security**: This endpoint is restricted to localhost and local network only.
        
        Returns:
            Number of expired entries deleted
            
        Example:
        ```bash
        curl -X POST "http://localhost:9123/cache/cleanup"
        ```
        """
        # Security check
        client_ip = request.client.host
        allowed_ips = ["127.0.0.1", "localhost", "::1"]
        is_local_network = client_ip.startswith("192.168.") or client_ip.startswith("10.") or client_ip.startswith("172.")
        
        if client_ip not in allowed_ips and not is_local_network:
            raise HTTPException(
                status_code=403,
                detail="Cache cleanup is restricted to localhost and local network only"
            )
        
        try:
            cache = get_cache()
            deleted = cache.cleanup_expired()
            
            logger.info(f"[CACHE] Cleaned up {deleted} expired entries (from {client_ip})")
            
            return {
                "ok": True,
                "deleted": deleted,
                "message": f"Cleaned up {deleted} expired cache entries"
            }
        except Exception as e:
            logger.error(f"[CACHE] Failed to cleanup cache: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
