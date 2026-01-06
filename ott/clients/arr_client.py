"""HTTP client for Radarr/Sonarr (*arr) APIs"""

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger("ott-hooks")


class ArrClient:
    """Base HTTP client for Radarr/Sonarr/etc APIs
    
    Provides safe HTTP methods with error handling and logging.
    All *arr applications use the same API structure at /api/v3/*.
    """
    
    def __init__(self, url: str, api_key: str, timeout: int = 120):
        """Initialize *arr API client
        
        Args:
            url: Base URL of the *arr instance (e.g., "http://localhost:7878")
            api_key: API key for authentication
            timeout: Request timeout in seconds (default: 120)
        """
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"X-Api-Key": api_key})
    
    def get(self, endpoint: str, **kwargs) -> Optional[requests.Response]:
        """Perform safe GET request
        
        Args:
            endpoint: API endpoint (e.g., "movie", "tag", "queue")
            **kwargs: Additional arguments passed to requests
        
        Returns:
            Response object if successful, None if failed
        """
        return self._request("GET", endpoint, **kwargs)
    
    def post(self, endpoint: str, **kwargs) -> Optional[requests.Response]:
        """Perform safe POST request
        
        Args:
            endpoint: API endpoint
            **kwargs: Additional arguments passed to requests (use json= for payload)
        
        Returns:
            Response object if successful, None if failed
        """
        return self._request("POST", endpoint, **kwargs)
    
    def put(self, endpoint: str, **kwargs) -> Optional[requests.Response]:
        """Perform safe PUT request
        
        Args:
            endpoint: API endpoint
            **kwargs: Additional arguments passed to requests (use json= for payload)
        
        Returns:
            Response object if successful, None if failed
        """
        return self._request("PUT", endpoint, **kwargs)
    
    def delete(self, endpoint: str, **kwargs) -> Optional[requests.Response]:
        """Perform safe DELETE request
        
        Args:
            endpoint: API endpoint
            **kwargs: Additional arguments passed to requests
        
        Returns:
            Response object if successful, None if failed
        """
        return self._request("DELETE", endpoint, **kwargs)
    
    def _request(
        self, 
        method: str, 
        endpoint: str, 
        **kwargs
    ) -> Optional[requests.Response]:
        """Internal method for making HTTP requests
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (without /api/v3 prefix)
            **kwargs: Additional arguments passed to requests
        
        Returns:
            Response object if successful, None if failed
        """
        # Build full URL
        url = f"{self.url}/api/v3/{endpoint}"
        
        # Set default timeout if not provided
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout
        
        try:
            res = self._session.request(method, url, **kwargs)
            
            if not res.ok:
                logger.error(
                    f"[HTTP] {method} {endpoint} failed → "
                    f"{res.status_code} {res.text[:200]}"
                )
                return None
            
            return res
            
        except requests.RequestException as e:
            logger.error(f"[HTTP] {method} {endpoint} request failed: {e}")
            return None
