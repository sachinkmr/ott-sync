"""JustWatch API client for OTT provider lookup"""

import logging
from typing import Optional

from simplejustwatchapi.justwatch import search

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
    
    def get_providers(
        self,
        title: str,
        year: int | None,
        allowed_providers: set[str]
    ) -> list[str] | None:
        """Search for title and return matching OTT providers
        
        Args:
            title: Movie or series title to search for
            year: Release year (optional, used for filtering)
            allowed_providers: Set of provider names to filter by
                (e.g., {"Netflix", "Prime Video", "Disney Plus Hotstar"})
        
        Returns:
            - List of matching provider names if found on OTT
            - Empty list if not found on any allowed providers
            - None if API call failed (infrastructure error)
        """
        logger.info(f"[JustWatch] Searching providers for '{title}' ({year})")
        
        try:
            results = search(title, self.region, self.language, self.max_results)
            
            for item in results:
                # Filter by year if provided
                if year and item.release_year != year:
                    continue
                
                # Check if available on any allowed providers
                if not item.offers:
                    continue
                
                found = [
                    offer.package.name
                    for offer in item.offers
                    if offer.package.name in allowed_providers
                ]
                
                if found:
                    logger.info(f"[JustWatch] FOUND on {found}")
                    return found
            
            # Searched but not found on any allowed providers
            logger.info("[JustWatch] Not found on allowed providers")
            return []
            
        except Exception as e:
            # API failure - return None to indicate infrastructure issue
            logger.warning(f"[JustWatch] Lookup failed: {e}")
            return None
