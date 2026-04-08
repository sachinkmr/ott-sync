"""Tests for JustWatchCache (TTL, hit/miss, invalidation, cleanup)."""

from datetime import datetime, timedelta

import pytest

from ott.db import client as db_client
from ott.db.client import DatabaseClient
from ott.db.schema import JustWatchCacheModel
from ott.utils.cache import JustWatchCache


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = DatabaseClient(db_path=str(tmp_path / "test.db"), enable_wal=False)
    d.initialize()
    monkeypatch.setattr(db_client, "db", d)
    yield d
    d.close(timeout=5.0)


@pytest.fixture
def cache(db):
    return JustWatchCache(
        ttl_found_days=7, ttl_not_found_hours=24, max_size_mb=50,
    )


class TestSetAndGet:
    def test_miss_returns_none(self, cache):
        assert cache.get("Unknown", 2020, "IN", "movie") is None

    def test_set_then_get_returns_providers(self, cache):
        cache.set(
            "Stranger Things", 2016, "IN", "series",
            providers=["Netflix"], tmdb_id=66732,
        )
        got = cache.get("Stranger Things", 2016, "IN", "series", tmdb_id=66732)
        assert got == ["Netflix"]

    def test_not_found_cached_returns_empty_list(self, cache):
        """Cached 'not on OTT' entries return [] (not None).

        This allows callers to distinguish cache-miss (None) from
        'confirmed not on OTT' ([]), avoiding unnecessary API re-queries.
        """
        cache.set("Indie Movie", 2023, "IN", "movie", providers=[], tmdb_id=1)
        stats = cache.get_stats()
        assert stats["total_entries"] == 1
        result = cache.get("Indie Movie", 2023, "IN", "movie", tmdb_id=1)
        assert result == []

    def test_region_isolation(self, cache):
        """Same movie in different regions caches independently."""
        cache.set("X", 2020, "IN", "movie", providers=["Netflix"], tmdb_id=5)
        cache.set("X", 2020, "US", "movie", providers=["Hulu"], tmdb_id=5)
        assert cache.get("X", 2020, "IN", "movie", tmdb_id=5) == ["Netflix"]
        assert cache.get("X", 2020, "US", "movie", tmdb_id=5) == ["Hulu"]

    def test_update_existing_entry(self, cache):
        """set() updates the existing row instead of creating a duplicate."""
        cache.set("X", 2020, "IN", "movie", providers=["Netflix"], tmdb_id=5)
        cache.set("X", 2020, "IN", "movie", providers=["Prime Video"], tmdb_id=5)
        assert cache.get("X", 2020, "IN", "movie", tmdb_id=5) == ["Prime Video"]

    def test_lookup_by_imdb_id_fallback(self, cache):
        cache.set("X", 2020, "IN", "movie", providers=["Netflix"], imdb_id="tt123")
        assert cache.get("X", 2020, "IN", "movie", imdb_id="tt123") == ["Netflix"]


class TestTTLExpiry:
    def test_expired_entry_returns_none(self, cache, db):
        """Entries past expires_at are not returned from get()."""
        # Manually insert an already-expired row
        with db.session() as session:
            entry = JustWatchCacheModel(
                tmdb_id=99, title="Old", year=2020, region="IN",
                item_type="movie", providers_json='["Netflix"]', found_on_ott=True,
                cached_at=datetime.utcnow() - timedelta(days=30),
                expires_at=datetime.utcnow() - timedelta(days=1),
                last_accessed=datetime.utcnow() - timedelta(days=30),
                cache_hit_count=0,
            )
            session.add(entry)
            session.commit()

        assert cache.get("Old", 2020, "IN", "movie", tmdb_id=99) is None


class TestHitRateTracking:
    def test_hits_and_misses_counted(self, cache):
        cache.get("Nothing Here", 2020, "IN", "movie", tmdb_id=11)  # miss
        cache.set("Present", 2020, "IN", "movie", providers=["Netflix"], tmdb_id=22)
        cache.get("Present", 2020, "IN", "movie", tmdb_id=22)  # hit
        cache.get("Present", 2020, "IN", "movie", tmdb_id=22)  # hit
        stats = cache.get_stats()
        assert stats["cache_hits"] == 2
        assert stats["cache_misses"] == 1
        assert stats["hit_rate_percent"] == round(2 / 3 * 100, 2)

    def test_hit_rate_zero_when_no_traffic(self, cache):
        stats = cache.get_stats()
        assert stats["hit_rate_percent"] == 0.0


class TestInvalidate:
    def test_invalidate_by_tmdb_id(self, cache):
        cache.set("A", 2020, "IN", "movie", providers=["Netflix"], tmdb_id=1)
        cache.set("B", 2021, "IN", "movie", providers=["Netflix"], tmdb_id=2)
        deleted = cache.invalidate(tmdb_id=1)
        assert deleted == 1
        assert cache.get("A", 2020, "IN", "movie", tmdb_id=1) is None
        assert cache.get("B", 2021, "IN", "movie", tmdb_id=2) == ["Netflix"]

    def test_invalidate_by_title_and_year(self, cache):
        cache.set("A", 2020, "IN", "movie", providers=["Netflix"])
        cache.set("A", 2021, "IN", "movie", providers=["Netflix"])
        deleted = cache.invalidate(title="A", year=2020)
        assert deleted == 1

    def test_invalidate_all_clears_cache(self, cache):
        cache.set("A", 2020, "IN", "movie", providers=["Netflix"], tmdb_id=1)
        cache.set("B", 2021, "IN", "movie", providers=["Netflix"], tmdb_id=2)
        assert cache.invalidate() == 2

    def test_invalidate_unknown_tmdb_id(self, cache):
        assert cache.invalidate(tmdb_id=99999) == 0


class TestCleanupExpired:
    def test_cleanup_removes_only_expired(self, cache, db):
        with db.session() as session:
            # One expired, one current
            session.add(JustWatchCacheModel(
                tmdb_id=1, title="Expired", year=2019, region="IN",
                item_type="movie", providers_json='["Netflix"]', found_on_ott=True,
                cached_at=datetime.utcnow() - timedelta(days=30),
                expires_at=datetime.utcnow() - timedelta(days=5),
                last_accessed=datetime.utcnow() - timedelta(days=30),
                cache_hit_count=0,
            ))
            session.add(JustWatchCacheModel(
                tmdb_id=2, title="Fresh", year=2024, region="IN",
                item_type="movie", providers_json='["Netflix"]', found_on_ott=True,
                cached_at=datetime.utcnow() - timedelta(hours=1),
                expires_at=datetime.utcnow() + timedelta(days=6),
                last_accessed=datetime.utcnow() - timedelta(hours=1),
                cache_hit_count=0,
            ))
            session.commit()

        deleted = cache.cleanup_expired()
        assert deleted == 1
        # Fresh entry still accessible
        assert cache.get("Fresh", 2024, "IN", "movie", tmdb_id=2) == ["Netflix"]

    def test_cleanup_noop_when_empty(self, cache):
        assert cache.cleanup_expired() == 0


class TestGetStats:
    def test_stats_shape(self, cache):
        cache.set("A", 2020, "IN", "movie", providers=["Netflix"], tmdb_id=1)
        stats = cache.get_stats()
        assert "total_entries" in stats
        assert "active_entries" in stats
        assert "expired_entries" in stats
        assert "cache_hits" in stats
        assert "cache_misses" in stats
        assert "hit_rate_percent" in stats
        assert stats["total_entries"] == 1
        assert stats["active_entries"] == 1
