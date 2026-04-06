"""OMDb API client for IMDb ratings lookup."""

import logging
from typing import Optional

import requests

from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("ott-hooks")


class OMDbClient:
    """Lightweight client for the Open Movie Database API (omdbapi.com).

    OMDb is the only public source that returns the actual IMDb score by
    imdb_id. Free tier: 1,000 requests/day. We use it exclusively for
    the ``imdbRating`` field; all other metadata comes from TMDB.

    Usage::

        client = OMDbClient(api_key="...", rate_limit_calls=50, rate_limit_period=60)
        rating = client.get_imdb_rating("tt1375666")  # Inception → 8.8
    """

    BASE_URL = "http://www.omdbapi.com/"

    def __init__(
        self,
        api_key: str,
        rate_limit_calls: int = 50,
        rate_limit_period: int = 60,
        timeout: int = 15,
    ):
        if not api_key:
            from ..exceptions import ConfigurationError
            raise ConfigurationError("OMDb API key is required")

        self.api_key = api_key
        self.timeout = timeout
        self.rate_limiter = RateLimiter(
            max_calls=rate_limit_calls,
            period_seconds=rate_limit_period,
        )
        self._session = requests.Session()
        logger.info(
            f"[OMDB] Initialized (rate limit: {rate_limit_calls}/{rate_limit_period}s)"
        )

    def get_imdb_rating(self, imdb_id: str) -> Optional[float]:
        """Fetch the IMDb rating for a title by its IMDb ID.

        Args:
            imdb_id: An IMDb title ID, e.g. ``"tt1375666"``.

        Returns:
            IMDb rating on a 0-10 scale, or None if the lookup fails, the
            title has no rating, or the rate limiter times out.
        """
        if not imdb_id:
            return None

        def _fetch() -> Optional[float]:
            try:
                res = self._session.get(
                    self.BASE_URL,
                    params={"i": imdb_id, "apikey": self.api_key},
                    timeout=self.timeout,
                )
                if not res.ok:
                    logger.error(f"[OMDB] HTTP {res.status_code} for {imdb_id}")
                    return None

                data = res.json()
                if data.get("Response") == "False":
                    logger.debug(f"[OMDB] No result for {imdb_id}: {data.get('Error')}")
                    return None

                raw = data.get("imdbRating", "N/A")
                if raw == "N/A":
                    return None
                return float(raw)
            except (requests.RequestException, ValueError) as e:
                logger.error(f"[OMDB] Fetch failed for {imdb_id}: {e}")
                return None

        return self.rate_limiter.execute(_fetch, timeout=self.timeout + 5)
