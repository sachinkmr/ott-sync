"""JustWatch API client for OTT provider lookup"""

import logging
from typing import Optional

from simplejustwatchapi.justwatch import search
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("ott-hooks")


class JustWatchClient:
    """Client for querying OTT availability via JustWatch API"""
    
    def __init__(
        self, 
        region: str = "IN", 
        language: str = "en", 
        max_results: int = 5,
        rate_limit_calls: int = 60,
        rate_limit_period: int = 60
    ):
        """Initialize JustWatch client
        
        Args:
            region: Country/region code (e.g., "IN", "US", "GB")
            language: Language code (e.g., "en", "es", "fr")
            max_results: Maximum number of search results to process
            rate_limit_calls: Maximum API calls allowed in period (default: 60)
            rate_limit_period: Time period in seconds (default: 60)
        """
        self.region = region
        self.language = language
        self.max_results = max_results
        
        # Configurable rate limiter to avoid API throttling
        self.rate_limiter = RateLimiter(
            max_calls=rate_limit_calls, 
            period_seconds=rate_limit_period
        )
        logger.info(f"[JustWatch] Rate limit: {rate_limit_calls} calls per {rate_limit_period}s")
    
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
                
                # Strategy: Check ID-matched results first (most accurate), 
                # then fall back to year-matched results (metadata resilience)
                id_matched_results = []
                year_matched_results = []
                
                for item in results:
                    provider_names = [offer.package.name for offer in item.offers]
                    logger.info(f"[JustWatch] Available on: {provider_names}")
                    
                    # Check if this result matches our IDs
                    id_match = False
                    if tmdb_id and hasattr(item, 'tmdb_id') and item.tmdb_id == tmdb_id:
                        id_match = True
                    elif imdb_id and hasattr(item, 'imdb_id') and item.imdb_id == imdb_id:
                        id_match = True
                    
                    # Categorize results by match quality
                    if id_match:
                        id_matched_results.append(item)
                    elif not year or abs(item.release_year - year) <= 1:
                        year_matched_results.append(item)
                
                # Check ID-matched results first (best accuracy)
                for item in id_matched_results:
                    found = [
                        offer.package.name
                        for offer in item.offers
                        if offer.package.name in allowed_providers
                    ]
                    if found:
                        logger.info(f"[JustWatch] FOUND on {found} (ID match)")
                        return found
                
                # Fall back to year-matched results (metadata resilience)
                for item in year_matched_results:
                    found = [
                        offer.package.name
                        for offer in item.offers
                        if offer.package.name in allowed_providers
                    ]
                    if found:
                        logger.info(f"[JustWatch] FOUND on {found} (year match, ID not available)")
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