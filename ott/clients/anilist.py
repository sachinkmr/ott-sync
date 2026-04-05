"""AniList GraphQL API client for anime identification"""

import logging
from typing import Optional

import requests

from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("ott-hooks")


class AniListAPIError(Exception):
    """AniList API error"""
    pass


class AniListRateLimitError(AniListAPIError):
    """AniList rate limit exceeded"""
    pass


class AniListClient:
    """Client for AniList GraphQL API
    
    AniList is an anime database with comprehensive metadata including:
    - AniList ID (anime-specific)
    - MyAnimeList (MAL) ID
    - AniDB ID (via cross-references)
    - Genres and formats
    
    Rate limit: 90 requests per 60 seconds
    API is free and requires no authentication
    """
    
    GRAPHQL_URL = "https://graphql.anilist.co"
    
    # GraphQL query for searching anime
    SEARCH_QUERY = """
    query ($search: String, $type: MediaType, $page: Int, $perPage: Int) {
      Page(page: $page, perPage: $perPage) {
        pageInfo {
          total
          currentPage
          hasNextPage
        }
        media(search: $search, type: $type) {
          id
          idMal
          title {
            romaji
            english
            native
          }
          startDate {
            year
            month
            day
          }
          format
          genres
          countryOfOrigin
          isAdult
        }
      }
    }
    """
    
    def __init__(
        self,
        rate_limit_calls: int = 90,
        rate_limit_period: int = 60,
        timeout: int = 30
    ):
        """Initialize AniList API client
        
        Args:
            rate_limit_calls: Maximum API calls allowed in period (default: 90)
            rate_limit_period: Time period in seconds (default: 60)
            timeout: Request timeout in seconds (default: 30)
        """
        self.timeout = timeout
        
        # Rate limiter to prevent API throttling
        self.rate_limiter = RateLimiter(
            max_calls=rate_limit_calls,
            period_seconds=rate_limit_period
        )
        
        # Reusable session
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        
        logger.info(
            f"[ANILIST] Initialized with rate limit: {rate_limit_calls} calls/{rate_limit_period}s"
        )
    
    def get_ratings(self, anilist_id: int) -> Optional[float]:
        """Fetch anime rating from AniList
        
        Args:
            anilist_id: AniList media ID
            
        Returns:
            Rating score (0-100 scale) or None on error
        """
        logger.debug(f"[ANILIST] Fetching rating for ID {anilist_id}")
        
        query = """
        query ($id: Int) {
          Media(id: $id, type: ANIME) {
            averageScore
          }
        }
        """
        
        variables = {"id": anilist_id}
        
        def _fetch():
            try:
                response = self._session.post(
                    self.GRAPHQL_URL,
                    json={"query": query, "variables": variables},
                    timeout=self.timeout
                )
                
                if response.status_code == 429:
                    raise AniListRateLimitError("AniList rate limit exceeded")
                
                if not response.ok:
                    logger.error(f"[ANILIST] Rating error {response.status_code}")
                    return None
                
                data = response.json()
                
                if "errors" in data:
                    logger.error(f"[ANILIST] GraphQL errors: {data['errors']}")
                    return None
                
                score = data.get("data", {}).get("Media", {}).get("averageScore")
                return score / 10.0 if score else None  # Convert to 0-10 scale
                
            except requests.RequestException as e:
                logger.error(f"[ANILIST] Rating request failed: {e}")
                return None
        
        try:
            return self.rate_limiter.execute(_fetch, timeout=self.timeout + 5)
        except Exception as e:
            logger.error(f"[ANILIST] Rating error: {e}")
            return None
    
    def search_anime(
        self,
        title: str,
        year: Optional[int] = None,
        per_page: int = 10
    ) -> list[dict]:
        """Search for anime by title with optional year filtering
        
        Args:
            title: Anime title to search (English, Romaji, or Native)
            year: Release year for filtering (±1 year tolerance)
            per_page: Maximum results to return (default: 10)
            
        Returns:
            List of matching anime with structure:
            [
                {
                    "id": 16498,
                    "idMal": 16498,
                    "title": {
                        "romaji": "Shingeki no Kyojin",
                        "english": "Attack on Titan",
                        "native": "進撃の巨人"
                    },
                    "startDate": {"year": 2013, "month": 4, "day": 7},
                    "format": "TV",
                    "genres": ["Action", "Drama", "Fantasy"],
                    "countryOfOrigin": "JP",
                    "isAdult": false
                }
            ]
            
            Returns empty list if no matches or error
        """
        logger.info(f"[ANILIST] Searching for '{title}'{f' ({year})' if year else ''}")
        
        def _search():
            try:
                payload = {
                    "query": self.SEARCH_QUERY,
                    "variables": {
                        "search": title,
                        "type": "ANIME",
                        "page": 1,
                        "perPage": per_page
                    }
                }
                
                response = self._session.post(
                    self.GRAPHQL_URL,
                    json=payload,
                    timeout=self.timeout
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    logger.warning(f"[ANILIST] Rate limit hit for '{title}'")
                    raise AniListRateLimitError("AniList rate limit exceeded")
                
                # Handle other errors
                if not response.ok:
                    logger.error(
                        f"[ANILIST] API error {response.status_code}: {response.text[:200]}"
                    )
                    raise AniListAPIError(f"HTTP {response.status_code}")
                
                data = response.json()
                
                # Check for GraphQL errors
                if "errors" in data:
                    errors = data["errors"]
                    logger.error(f"[ANILIST] GraphQL errors: {errors}")
                    raise AniListAPIError(f"GraphQL errors: {errors}")
                
                # Extract media list
                media_list = data.get("data", {}).get("Page", {}).get("media", [])
                
                # Filter by year if provided (±1 year tolerance)
                if year and media_list:
                    original_count = len(media_list)
                    media_list = [
                        m for m in media_list
                        if m.get("startDate", {}).get("year")
                        and abs(m["startDate"]["year"] - year) <= 1
                    ]
                    if len(media_list) < original_count:
                        logger.info(
                            f"[ANILIST] Filtered by year: {original_count} → {len(media_list)} results"
                        )
                
                # Log results
                if media_list:
                    logger.info(f"[ANILIST] ✓ Found {len(media_list)} matches for '{title}'")
                    for idx, anime in enumerate(media_list[:3]):  # Log first 3
                        title_info = anime.get("title", {})
                        romaji = title_info.get("romaji", "?")
                        year_val = anime.get("startDate", {}).get("year", "?")
                        format_val = anime.get("format", "?")
                        logger.info(f"[ANILIST]   #{idx+1}: {romaji} ({year_val}) [{format_val}]")
                else:
                    logger.info(f"[ANILIST] No matches for '{title}'")
                
                return media_list
                
            except requests.Timeout:
                logger.error(f"[ANILIST] Request timeout for '{title}'")
                raise AniListAPIError("Request timeout")
            except requests.RequestException as e:
                logger.error(f"[ANILIST] Request failed for '{title}': {e}")
                raise AniListAPIError(f"Request failed: {e}")
            except (KeyError, ValueError) as e:
                logger.error(f"[ANILIST] Response parse error for '{title}': {e}")
                raise AniListAPIError(f"Parse error: {e}")
        
        # Execute with rate limiting
        try:
            result = self.rate_limiter.execute(_search, timeout=self.timeout + 5)
            return result or []
        except AniListRateLimitError:
            # Rate limit hit - let rate limiter handle retry
            logger.warning("[ANILIST] Rate limit exceeded, will retry automatically")
            return []
        except AniListAPIError as e:
            # Other API error - fail gracefully
            logger.error(f"[ANILIST] API error: {e}")
            return []
        except Exception as e:
            # Unexpected error
            logger.error(f"[ANILIST] Unexpected error: {e}")
            return []
    
    def get_best_match(
        self,
        results: list[dict],
        title: str,
        year: Optional[int] = None
    ) -> Optional[dict]:
        """Get best matching result from search results
        
        Prioritizes:
        1. Exact title match (any language)
        2. Year match (if provided)
        3. TV format over movies/OVAs
        4. First result (AniList ranks by relevance)
        
        Args:
            results: List of results from search_anime()
            title: Original search title
            year: Original search year
            
        Returns:
            Best matching anime dict or None
        """
        if not results:
            return None
        
        title_lower = title.lower()
        
        # Try exact title match first
        for result in results:
            titles = result.get("title", {})
            for title_type in ["english", "romaji", "native"]:
                result_title = titles.get(title_type, "")
                if result_title and result_title.lower() == title_lower:
                    logger.info(f"[ANILIST] Exact title match: {result_title}")
                    return result
        
        # Try year match (if provided)
        if year:
            for result in results:
                result_year = result.get("startDate", {}).get("year")
                if result_year == year:
                    logger.info(f"[ANILIST] Year match: {year}")
                    return result
        
        # Prefer TV format
        for result in results:
            if result.get("format") == "TV":
                logger.info("[ANILIST] TV format match")
                return result
        
        # Default to first result (most relevant)
        logger.info("[ANILIST] Using first result (most relevant)")
        return results[0]
    
    def has_anime_id(self, anime_data: dict) -> bool:
        """Check if anime has valid AniList or MAL ID
        
        Args:
            anime_data: Anime dict from search_anime()
            
        Returns:
            True if has valid anime ID
        """
        anilist_id = anime_data.get("id")
        mal_id = anime_data.get("idMal")
        
        return bool(anilist_id or mal_id)
    
    def extract_ids(self, anime_data: dict) -> dict[str, int]:
        """Extract all available IDs from anime data
        
        Args:
            anime_data: Anime dict from search_anime()
            
        Returns:
            Dict with available IDs: {"anilist": 16498, "mal": 16498}
        """
        ids = {}
        
        if anime_data.get("id"):
            ids["anilist"] = anime_data["id"]
        
        if anime_data.get("idMal"):
            ids["mal"] = anime_data["idMal"]
        
        return ids
    
    def get_title(self, anime_data: dict, prefer: str = "english") -> str:
        """Get title from anime data
        
        Args:
            anime_data: Anime dict from search_anime()
            prefer: Preferred title language ("english", "romaji", "native")
            
        Returns:
            Title string (falls back to first available)
        """
        titles = anime_data.get("title", {})
        
        # Try preferred language
        if prefer in titles and titles[prefer]:
            return titles[prefer]
        
        # Fallback order: english → romaji → native
        for key in ["english", "romaji", "native"]:
            if titles.get(key):
                return titles[key]
        
        return "Unknown"
