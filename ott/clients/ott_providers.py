"""Unified OTT provider lookup with TMDB primary, JustWatch fallback, and
a local SQLite cache layer that avoids hitting either API for repeated lookups.

Cache TTL: 30 days (configurable). After TTL, the next lookup re-queries the
APIs and refreshes the cache row. Manual overrides (inserted via the
``set_manual`` method or a future CLI command) use the same cache table and
follow the same TTL, keeping the data fresh.
"""

import logging
from typing import Optional

import requests

from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("ott-hooks")

# Sentinel to distinguish "cache miss" from "cached as not-on-OTT ([])"
_CACHE_MISS = None


class OTTProviderClient:
    """Dual-backend OTT availability checker with a local cache.

    Lookup order:
    1. Local SQLite cache (``JustWatchCache``) — instant, no network.
    2. TMDB ``/watch/providers`` (by tmdb_id, exact, fast).
    3. JustWatch GraphQL (by title search, fuzzy, slower).

    Results from steps 2-3 are stored in the cache for ``cache_ttl_days``
    (default 30). Manual overrides go into the same cache and follow the
    same TTL so they auto-refresh.

    Usage::

        client = OTTProviderClient(
            tmdb_api_key="...",
            region="IN",
            justwatch_client=existing_jw_client,
            cache=existing_cache_instance,
        )
        providers = client.get_providers(
            title="Stranger Things",
            year=2016,
            allowed_providers={"Netflix"},
            tmdb_id=66732,
            media_type="tv",
        )
    """

    TMDB_BASE = "https://api.themoviedb.org/3"

    def __init__(
        self,
        tmdb_api_key: str,
        region: str = "IN",
        justwatch_client=None,
        cache=None,
        cache_ttl_days: int = 30,
        rate_limit_calls: int = 40,
        rate_limit_period: int = 10,
        timeout: int = 15,
    ):
        self.tmdb_api_key = tmdb_api_key
        self.region = region
        self.justwatch = justwatch_client
        self.cache = cache
        self.cache_ttl_days = cache_ttl_days
        self.timeout = timeout
        self._session = requests.Session()
        self.rate_limiter = RateLimiter(
            max_calls=rate_limit_calls,
            period_seconds=rate_limit_period,
        )
        logger.info(
            f"[OTT] Initialized dual-backend provider client "
            f"(TMDB primary @ {rate_limit_calls}/{rate_limit_period}s, "
            f"JustWatch fallback {'enabled' if justwatch_client else 'disabled'}, "
            f"cache {'enabled' if cache else 'disabled'})"
        )

    def get_providers(
        self,
        title: str,
        year: Optional[int],
        allowed_providers: set[str],
        tmdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
        media_type: str = "movie",
    ) -> Optional[list[str]]:
        """Check OTT availability for a title.

        Lookup order: cache → TMDB → JustWatch. Results are cached for
        ``cache_ttl_days`` (default 30). Empty results ("not on OTT") are
        also cached so we don't re-query every cron cycle.

        Args:
            title: Movie or series title (used for JustWatch fallback).
            year: Release year (used for JustWatch fallback).
            allowed_providers: Set of provider names the user cares about.
            tmdb_id: TMDB ID — when present, uses the fast TMDB path.
            imdb_id: IMDb ID (passed to JustWatch fallback only).
            media_type: "movie" or "series"/"tv".

        Returns:
            List of matching provider names if found on OTT.
            Empty list if not found on any allowed providers.
            None if all lookups failed (infrastructure error).
        """
        tmdb_type = "tv" if media_type in ("series", "tv") else "movie"

        # ── 1. Check local cache ──
        if self.cache:
            cached = self.cache.get(
                title, year, self.region, tmdb_type,
                tmdb_id=tmdb_id, imdb_id=imdb_id,
            )
            if cached is not None:
                # cached is [] (not on OTT) or ["Netflix", ...] (on OTT)
                found = [p for p in cached if p in allowed_providers]
                if found:
                    logger.info(f"[OTT-CACHE] HIT '{title}' → {found}")
                    return found
                if cached:
                    # Cached providers exist but none match allowed list
                    logger.debug(f"[OTT-CACHE] HIT '{title}' but not on allowed (cached: {cached})")
                    return []
                # cached == [] means "confirmed not on OTT" in cache
                logger.debug(f"[OTT-CACHE] HIT '{title}' → not on OTT (cached empty)")
                return []

        # ── 2. TMDB Watch Providers (primary) ──
        result = _CACHE_MISS
        if tmdb_id and self.tmdb_api_key:
            result = self._tmdb_lookup(tmdb_id, tmdb_type, allowed_providers, title)
            if result is None:
                pass  # API failure → fall through to JW
            elif result:
                self._store_in_cache(title, year, tmdb_type, result, tmdb_id, imdb_id)
                return result

        # ── 3. JustWatch fallback / second opinion ──
        if self.justwatch:
            logger.info(f"[OTT] Checking JustWatch for '{title}'")
            jw_result = self.justwatch.get_providers(
                title, year, allowed_providers,
                tmdb_id=tmdb_id, imdb_id=imdb_id,
            )
            if jw_result is not None:
                self._store_in_cache(title, year, tmdb_type, jw_result, tmdb_id, imdb_id)
                return jw_result

        # ── 4. Both failed → store empty if TMDB returned [] ──
        if result is not None and not result:
            # TMDB said "not on allowed providers" and JW also failed/empty
            self._store_in_cache(title, year, tmdb_type, [], tmdb_id, imdb_id)
            return []

        logger.error(f"[OTT] All backends failed for '{title}'")
        return None

    def _store_in_cache(
        self,
        title: str,
        year: Optional[int],
        item_type: str,
        providers: list[str],
        tmdb_id: Optional[int],
        imdb_id: Optional[str],
    ) -> None:
        """Store a lookup result in the local cache."""
        if not self.cache:
            return
        # Override the cache's built-in TTL to use our 30-day window
        self.cache.ttl_found_days = self.cache_ttl_days
        self.cache.ttl_not_found_hours = self.cache_ttl_days * 24
        self.cache.set(
            title, year, self.region, item_type,
            providers=providers,
            tmdb_id=tmdb_id, imdb_id=imdb_id,
        )

    def set_manual(
        self,
        title: str,
        year: Optional[int],
        providers: list[str],
        media_type: str = "movie",
        tmdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> bool:
        """Manually set OTT provider availability for a title.

        Inserts (or updates) a cache row so the next lookup returns these
        providers without hitting any API. Follows the same TTL as regular
        cached entries (default 30 days), after which the APIs re-check.

        Use this for titles where TMDB + JustWatch have data gaps (e.g.
        The Simpsons on JioHotstar in India).

        Args:
            title: Movie or series title.
            year: Release year.
            providers: List of provider names (e.g. ["JioHotstar"]).
            media_type: "movie" or "series"/"tv".
            tmdb_id: Optional TMDB ID for cache key.
            imdb_id: Optional IMDb ID for cache key.

        Returns:
            True if stored successfully.
        """
        if not self.cache:
            logger.error("[OTT] Cannot set manual override: no cache configured")
            return False
        item_type = "tv" if media_type in ("series", "tv") else "movie"
        self._store_in_cache(title, year, item_type, providers, tmdb_id, imdb_id)
        logger.info(f"[OTT-MANUAL] Set '{title}' → {providers} (TTL {self.cache_ttl_days}d)")
        return True

    def _tmdb_lookup(
        self,
        tmdb_id: int,
        media_type: str,
        allowed_providers: set[str],
        title: str,
    ) -> Optional[list[str]]:
        """Query TMDB /watch/providers for a specific title.

        Returns:
            List of matching provider names (may be empty).
            None on API failure (caller should fall back to JustWatch).
        """
        def _fetch():
            try:
                url = f"{self.TMDB_BASE}/{media_type}/{tmdb_id}/watch/providers"
                res = self._session.get(
                    url,
                    params={"api_key": self.tmdb_api_key},
                    timeout=self.timeout,
                )
                if not res.ok:
                    logger.error(
                        f"[OTT-TMDB] HTTP {res.status_code} for "
                        f"{media_type}/{tmdb_id}"
                    )
                    return None

                data = res.json()
                region_data = data.get("results", {}).get(self.region)
                if not region_data:
                    logger.info(
                        f"[OTT-TMDB] No providers for '{title}' in {self.region}"
                    )
                    return []

                # Collect providers from flatrate + ads tiers (streaming)
                providers = set()
                for tier in ("flatrate", "ads"):
                    for p in region_data.get(tier, []):
                        name = p.get("provider_name", "")
                        if name:
                            providers.add(name)

                found = [p for p in providers if p in allowed_providers]
                if found:
                    logger.info(f"[OTT-TMDB] FOUND '{title}' on {found}")
                else:
                    all_names = list(providers) if providers else ["none"]
                    logger.info(
                        f"[OTT-TMDB] '{title}' not on allowed providers "
                        f"(available: {all_names})"
                    )
                return found

            except requests.RequestException as e:
                logger.error(f"[OTT-TMDB] Request failed for {media_type}/{tmdb_id}: {e}")
                return None

        return self.rate_limiter.execute(_fetch, timeout=60.0)
