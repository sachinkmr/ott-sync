"""Sonarr manager for series governance"""

from typing import Any

from .base import OTTBaseManager


class SonarrManager(OTTBaseManager):
    """OTT manager for Sonarr (TV series)"""
    
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
