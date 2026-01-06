"""Radarr manager for movie governance"""

from typing import Any

from .base import OTTBaseManager


class RadarrManager(OTTBaseManager):
    """OTT manager for Radarr (movies)"""
    
    def fetch_items(self) -> list[dict[str, Any]]:
        """Fetch all movies from Radarr
        
        Returns:
            List of movie dictionaries
        """
        res = self.client.get("movie")
        if not res:
            return []
        return res.json()
    
    def item_type(self) -> str:
        """Get item type for Radarr
        
        Returns:
            "movie"
        """
        return "movie"
