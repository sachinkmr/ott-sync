"""Unified OTT provider lookup with TMDB primary, JustWatch fallback.

TMDB's /watch/providers endpoint returns the same JustWatch-sourced data
but through an official, key-authenticated API with transparent rate limits
(40 req/10s) and no IP bans. Falls back to JustWatch title-search for items
missing a tmdb_id (~5% of catalogue).
"""

import logging
from typing import Optional

import requests

from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("ott-hooks")


class OTTProviderClient:
    """Dual-backend OTT availability checker.

    Primary:  TMDB ``/watch/providers`` (by tmdb_id, exact, fast)
    Fallback: JustWatch GraphQL (by title search, fuzzy, slower)

    Both share the same underlying provider data (TMDB sources from JustWatch).

    Usage::

        client = OTTProviderClient(
            tmdb_api_key="...",
            region="IN",
            justwatch_client=existing_jw_client,  # for fallback
        )
        providers = client.get_providers(
            title="Stranger Things",
            year=2016,
            allowed_providers={"Netflix", "Prime Video"},
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
        rate_limit_calls: int = 40,
        rate_limit_period: int = 10,
        timeout: int = 15,
    ):
        self.tmdb_api_key = tmdb_api_key
        self.region = region
        self.justwatch = justwatch_client
        self.timeout = timeout
        self._session = requests.Session()
        self.rate_limiter = RateLimiter(
            max_calls=rate_limit_calls,
            period_seconds=rate_limit_period,
        )
        logger.info(
            f"[OTT] Initialized dual-backend provider client "
            f"(TMDB primary @ {rate_limit_calls}/{rate_limit_period}s, "
            f"JustWatch fallback {'enabled' if justwatch_client else 'disabled'})"
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
        # Normalize media type for TMDB endpoint
        tmdb_type = "tv" if media_type in ("series", "tv") else "movie"

        # ── Primary: TMDB Watch Providers (by tmdb_id) ──
        if tmdb_id and self.tmdb_api_key:
            result = self._tmdb_lookup(tmdb_id, tmdb_type, allowed_providers, title)
            if result is not None:
                return result
            # result is None means TMDB lookup failed → fall through to JW

        # ── Fallback: JustWatch title search ──
        if self.justwatch:
            logger.info(
                f"[OTT] Falling back to JustWatch for '{title}' "
                f"({'no tmdb_id' if not tmdb_id else 'TMDB failed'})"
            )
            return self.justwatch.get_providers(
                title, year, allowed_providers,
                tmdb_id=tmdb_id, imdb_id=imdb_id,
            )

        logger.error(f"[OTT] No backend available for '{title}'")
        return None

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
