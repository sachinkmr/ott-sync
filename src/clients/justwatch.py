"""JustWatch API client for OTT provider lookup"""

import logging
from typing import Optional

from simplejustwatchapi.justwatch import search
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("ott-hooks")


class JustWatchClient:
    """Client for querying OTT availability via JustWatch API"""
    
    def __init__(self, region: str = "IN", language: str = "en", max_results: int = 5):
        """Initialize JustWatch client
        
        Args:
            region: Country/region code (e.g., "IN", "US", "GB")
            language: Language code (e.g., "en", "es", "fr")
            max_results: Maximum number of search results to process
        """
        self.region = region
        self.language = language
        self.max_results = max_results
        
        # Rate limiter: 10 calls per minute to avoid API throttling
        self.rate_limiter = RateLimiter(max_calls=10, period_seconds=60)
    
    def get_providers(
        self,
        title: str,
        year: int | None,
        allowed_providers: set[str],
        tmdb_id: int | None = None,
        imdb_id: str | None = None
    ) -> list[str] | None:
        """Search for title and return matching OTT providers
        
        Args:
            title: Movie or series title to search for
            year: Release year (optional, used for filtering)
            allowed_providers: Set of provider names to filter by
                (e.g., {"Netflix", "Prime Video", "Disney Plus Hotstar"})
            tmdb_id: TMDb ID for more accurate matching (optional)
            imdb_id: IMDb ID for more accurate matching (optional)
        
        Returns:
            - List of matching provider names if found on OTT
            - Empty list if not found on any allowed providers
            - None if API call failed (infrastructure error)
        """
        id_info = f" (TMDb: {tmdb_id}, IMDb: {imdb_id})" if tmdb_id or imdb_id else ""
        logger.info(f"[JustWatch] Searching providers for '{title}' ({year}){id_info}")
        
        # Use rate limiter to avoid API throttling
        def _search():
            try:
                results = search(title, self.region, self.language, self.max_results)
                
                for item in results:
                    # Prefer ID-based matching if available (most accurate)
                    id_match = False
                    if tmdb_id and hasattr(item, 'tmdb_id'):
                        id_match = item.tmdb_id == tmdb_id
                    elif imdb_id and hasattr(item, 'imdb_id'):
                        id_match = item.imdb_id == imdb_id
                    
                    # If IDs provided but don't match, skip
                    if (tmdb_id or imdb_id) and not id_match:
                        continue
                    
                    # Filter by year if provided (with ±1 year tolerance)
                    # Skip year check if ID matched (year can differ for multi-season shows)
                    if not id_match and year and abs(item.release_year - year) > 1:
                        continue
                    
                    # Check if available on any allowed providers
                    if not item.offers:
                        continue
                    
                    # Only consider subscription streaming (flatrate), not rent/buy
                    found = [
                        offer.package.name
                        for offer in item.offers
                        if offer.package.name in allowed_providers
                        and offer.monetization_type == "flatrate"  # Subscription only!
                    ]
                    
                    if found:
                        logger.info(f"[JustWatch] FOUND on {found} (subscription)")
                        return found
                
                # Searched but not found on any allowed providers
                logger.info("[JustWatch] Not found on allowed providers")
                return []
            except Exception as e:
                logger.error(f"[JustWatch] Search failed: {e}")
                return None
        
        # Execute with rate limiting
        result = self.rate_limiter.execute(_search, timeout=30.0)
        
        if result is None:
            logger.error("[JustWatch] Rate limit timeout or execution failed")
            return None  # Fail-open
        
        return result