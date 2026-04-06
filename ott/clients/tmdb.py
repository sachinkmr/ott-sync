"""TMDB API client for series metadata lookup"""

import logging
from typing import Optional

import requests

from ..exceptions import ConfigurationError
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("ott-hooks")


class TMDBAPIError(Exception):
    """TMDB API error"""
    pass


class TMDBRateLimitError(TMDBAPIError):
    """TMDB rate limit exceeded"""
    pass


class TMDBClient:
    """Client for TMDB (The Movie Database) API v3
    
    Provides methods to fetch TV series metadata including:
    - Genres and original language
    - Keywords (for anime detection)
    - External IDs (IMDb, TVDB)
    
    Rate limit: 40 requests per 10 seconds (free tier)
    """
    
    BASE_URL = "https://api.themoviedb.org/3"
    
    def __init__(
        self,
        api_key: str,
        rate_limit_calls: int = 40,
        rate_limit_period: int = 10,
        timeout: int = 30
    ):
        """Initialize TMDB API client
        
        Args:
            api_key: TMDB API key (get from https://www.themoviedb.org/settings/api)
            rate_limit_calls: Maximum API calls allowed in period (default: 40)
            rate_limit_period: Time period in seconds (default: 10)
            timeout: Request timeout in seconds (default: 30)
        """
        if not api_key:
            raise ConfigurationError("TMDB API key is required")
        
        self.api_key = api_key
        self.timeout = timeout
        
        # Rate limiter to prevent API throttling
        self.rate_limiter = RateLimiter(
            max_calls=rate_limit_calls,
            period_seconds=rate_limit_period
        )
        
        # Reusable session for connection pooling
        self._session = requests.Session()
        
        logger.info(
            f"[TMDB] Initialized with rate limit: {rate_limit_calls} calls/{rate_limit_period}s"
        )
    
    def get_series_details(
        self,
        tmdb_id: int,
        include_keywords: bool = True,
        include_external_ids: bool = True
    ) -> Optional[dict]:
        """Fetch TV series details from TMDB
        
        Args:
            tmdb_id: TMDB series ID (from Sonarr)
            include_keywords: Include keywords in response (default: True)
            include_external_ids: Include external IDs in response (default: True)
            
        Returns:
            Series metadata dict with structure:
            {
                "id": 1429,
                "name": "Attack on Titan",
                "original_language": "ja",
                "origin_country": ["JP"],
                "genres": [
                    {"id": 16, "name": "Animation"},
                    {"id": 10759, "name": "Action & Adventure"}
                ],
                "keywords": {
                    "results": [
                        {"id": 210024, "name": "anime"},
                        {"id": 9711, "name": "manga"}
                    ]
                },
                "external_ids": {
                    "imdb_id": "tt2560140",
                    "tvdb_id": 267440
                }
            }
            
            Returns None if API call fails
        """
        logger.info(f"[TMDB] Fetching series details for tmdb_id={tmdb_id}")
        
        def _fetch():
            try:
                # Build append_to_response parameter
                append_parts = []
                if include_keywords:
                    append_parts.append("keywords")
                if include_external_ids:
                    append_parts.append("external_ids")
                
                url = f"{self.BASE_URL}/tv/{tmdb_id}"
                params = {
                    "api_key": self.api_key
                }
                
                if append_parts:
                    params["append_to_response"] = ",".join(append_parts)
                
                response = self._session.get(url, params=params, timeout=self.timeout)
                
                # Handle rate limiting
                if response.status_code == 429:
                    logger.warning(f"[TMDB] Rate limit hit for tmdb_id={tmdb_id}")
                    raise TMDBRateLimitError("TMDB rate limit exceeded")
                
                # Handle other errors
                if not response.ok:
                    logger.error(
                        f"[TMDB] API error {response.status_code} for tmdb_id={tmdb_id}: "
                        f"{response.text[:200]}"
                    )
                    raise TMDBAPIError(f"HTTP {response.status_code}")
                
                data = response.json()
                
                # Log summary
                name = data.get("name", "Unknown")
                language = data.get("original_language", "?")
                genres = [g.get("name") for g in data.get("genres", [])]
                keywords = [k.get("name") for k in data.get("keywords", {}).get("results", [])]
                
                logger.info(
                    f"[TMDB] ✓ {name} | language={language} | "
                    f"genres={genres} | keywords={len(keywords)} items"
                )
                
                return data
                
            except requests.Timeout:
                logger.error(f"[TMDB] Request timeout for tmdb_id={tmdb_id}")
                raise TMDBAPIError("Request timeout")
            except requests.RequestException as e:
                logger.error(f"[TMDB] Request failed for tmdb_id={tmdb_id}: {e}")
                raise TMDBAPIError(f"Request failed: {e}")
            except (KeyError, ValueError) as e:
                logger.error(f"[TMDB] Response parse error for tmdb_id={tmdb_id}: {e}")
                raise TMDBAPIError(f"Parse error: {e}")
        
        # Execute with rate limiting
        try:
            result = self.rate_limiter.execute(_fetch, timeout=self.timeout + 5)
            return result
        except TMDBRateLimitError:
            # Rate limit hit - let rate limiter handle retry
            logger.warning("[TMDB] Rate limit exceeded, will retry automatically")
            return None
        except TMDBAPIError as e:
            # Other API error - fail gracefully
            logger.error(f"[TMDB] API error: {e}")
            return None
        except Exception as e:
            # Unexpected error
            logger.error(f"[TMDB] Unexpected error: {e}")
            return None
    
    def search_series(self, query: str, year: Optional[int] = None) -> list[dict]:
        """Search for TV series by title
        
        Args:
            query: Series title to search
            year: Release year for filtering (optional)
            
        Returns:
            List of series results (empty if none found or error)
        """
        logger.info(f"[TMDB] Searching for '{query}'{f' ({year})' if year else ''}")
        
        def _search():
            try:
                url = f"{self.BASE_URL}/search/tv"
                params = {
                    "api_key": self.api_key,
                    "query": query
                }
                
                if year:
                    params["first_air_date_year"] = year
                
                response = self._session.get(url, params=params, timeout=self.timeout)
                
                if response.status_code == 429:
                    raise TMDBRateLimitError("TMDB rate limit exceeded")
                
                if not response.ok:
                    logger.error(
                        f"[TMDB] Search error {response.status_code}: {response.text[:200]}"
                    )
                    return []
                
                data = response.json()
                results = data.get("results", [])
                
                logger.info(f"[TMDB] Found {len(results)} results for '{query}'")
                return results
                
            except requests.RequestException as e:
                logger.error(f"[TMDB] Search request failed: {e}")
                return []
        
        try:
            return self.rate_limiter.execute(_search, timeout=self.timeout + 5) or []
        except Exception as e:
            logger.error(f"[TMDB] Search error: {e}")
            return []
    
    def extract_genres(self, series_data: dict) -> list[str]:
        """Extract genre names from TMDB series data
        
        Args:
            series_data: Series data from get_series_details()
            
        Returns:
            List of genre names (e.g., ["Animation", "Action & Adventure"])
        """
        genres = series_data.get("genres", [])
        return [g.get("name") for g in genres if g.get("name")]
    
    def extract_keywords(self, series_data: dict) -> list[str]:
        """Extract keyword names from TMDB series data
        
        Args:
            series_data: Series data from get_series_details()
            
        Returns:
            List of keyword names (e.g., ["anime", "manga", "shounen"])
        """
        keywords = series_data.get("keywords", {}).get("results", [])
        return [k.get("name") for k in keywords if k.get("name")]
    
    def get_original_language(self, series_data: dict) -> Optional[str]:
        """Extract original language code from TMDB series data
        
        Args:
            series_data: Series data from get_series_details()
            
        Returns:
            ISO 639-1 language code (e.g., "ja", "en", "zh") or None
        """
        return series_data.get("original_language")
    
    def get_origin_countries(self, series_data: dict) -> list[str]:
        """Extract origin country codes from TMDB series data
        
        Args:
            series_data: Series data from get_series_details()
            
        Returns:
            List of ISO 3166-1 country codes (e.g., ["JP"], ["US", "GB"])
        """
        return series_data.get("origin_country", [])
    
    def get_movie_ratings(self, tmdb_id: int) -> Optional[dict]:
        """Fetch movie ratings from TMDB (includes TMDb score and IMDb ID)
        
        Args:
            tmdb_id: TMDB movie ID
            
        Returns:
            Dict with ratings: {"tmdb": 8.5, "imdb_id": "tt1234567"} or None on error
        """
        logger.debug(f"[TMDB] Fetching ratings for movie ID {tmdb_id}")
        
        def _fetch():
            try:
                url = f"{self.BASE_URL}/movie/{tmdb_id}"
                params = {"api_key": self.api_key}
                
                response = self._session.get(url, params=params, timeout=self.timeout)
                
                if response.status_code == 429:
                    raise TMDBRateLimitError("TMDB rate limit exceeded")
                
                if not response.ok:
                    logger.error(f"[TMDB] Movie ratings error {response.status_code}")
                    return None
                
                data = response.json()
                
                return {
                    "tmdb": data.get("vote_average"),
                    "imdb_id": data.get("imdb_id")
                }
                
            except requests.RequestException as e:
                logger.error(f"[TMDB] Movie ratings request failed: {e}")
                return None
        
        try:
            return self.rate_limiter.execute(_fetch, timeout=self.timeout + 5)
        except Exception as e:
            logger.error(f"[TMDB] Movie ratings error: {e}")
            return None
    
    def get_series_ratings(self, tmdb_id: int) -> Optional[dict]:
        """Fetch series ratings from TMDB
        
        Args:
            tmdb_id: TMDB series ID
            
        Returns:
            Dict with ratings: {"tmdb": 8.5} or None on error
        """
        # Series details already fetched contain vote_average
        series_data = self.get_series_details(tmdb_id, include_keywords=False, include_external_ids=False)
        
        if not series_data:
            return None
        
        return {"tmdb": series_data.get("vote_average")}

    # -------------------- Rating gate helpers --------------------

    def get_rating(
        self,
        tmdb_id: int,
        media_type: str = "movie",
        min_vote_count: int = 50,
    ) -> Optional[dict]:
        """Fetch a normalized rating for use by the rating gate.

        Args:
            tmdb_id: TMDB movie or series ID.
            media_type: "movie" or "tv".
            min_vote_count: Minimum vote count to trust the score. Returns
                None when there are fewer votes than this.

        Returns:
            {"score_pct": 82.0, "vote_count": 1234, "source": "tmdb"} or None
            when the lookup fails or the vote count is below threshold.
        """
        def _fetch():
            try:
                if media_type == "movie":
                    url = f"{self.BASE_URL}/movie/{tmdb_id}"
                else:
                    url = f"{self.BASE_URL}/tv/{tmdb_id}"
                params = {"api_key": self.api_key}
                response = self._session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 429:
                    raise TMDBRateLimitError("TMDB rate limit exceeded")
                if not response.ok:
                    return None
                data = response.json()
                vote_avg = data.get("vote_average")
                vote_count = data.get("vote_count", 0)
                if vote_avg is None or vote_count < min_vote_count:
                    return None
                return {
                    "score_pct": round(vote_avg * 10, 1),  # 0-10 → 0-100
                    "vote_count": vote_count,
                    "source": "tmdb",
                }
            except requests.RequestException as e:
                logger.error(f"[TMDB] get_rating failed for {media_type}/{tmdb_id}: {e}")
                return None

        try:
            return self.rate_limiter.execute(_fetch, timeout=self.timeout + 5)
        except Exception as e:
            logger.error(f"[TMDB] get_rating error: {e}")
            return None

    def is_trending(
        self,
        tmdb_id: int,
        media_type: str = "movie",
        window: str = "week",
    ) -> bool:
        """Check if a title is on the TMDB trending list.

        Args:
            tmdb_id: TMDB movie or series ID.
            media_type: "movie" or "tv".
            window: "day" or "week" (TMDB endpoint parameter).

        Returns:
            True if the title appears in the first page of trending results.
        """
        def _fetch():
            try:
                url = f"{self.BASE_URL}/trending/{media_type}/{window}"
                params = {"api_key": self.api_key}
                response = self._session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 429:
                    raise TMDBRateLimitError("TMDB rate limit exceeded")
                if not response.ok:
                    return False
                data = response.json()
                ids = {item["id"] for item in data.get("results", [])}
                return tmdb_id in ids
            except requests.RequestException as e:
                logger.error(f"[TMDB] is_trending failed: {e}")
                return False

        try:
            result = self.rate_limiter.execute(_fetch, timeout=self.timeout + 5)
            return result if result is not None else False
        except Exception as e:
            logger.error(f"[TMDB] is_trending error: {e}")
            return False
