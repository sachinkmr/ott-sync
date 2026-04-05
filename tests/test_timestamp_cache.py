"""Tests for the file-backed TimestampCache."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from ott.utils.timestamp_cache import TimestampCache, _parse_timestamp, _utcnow


def test_utcnow_is_timezone_aware():
    """_utcnow returns a UTC-aware datetime."""
    now = _utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_parse_timestamp_accepts_aware_iso():
    parsed = _parse_timestamp("2026-04-05T12:00:00+00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_timestamp_normalizes_naive_legacy():
    """Legacy naive timestamps are interpreted as UTC for backward compat."""
    parsed = _parse_timestamp("2026-04-05T12:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_parse_timestamp_returns_none_on_garbage():
    assert _parse_timestamp("not-a-timestamp") is None
    assert _parse_timestamp("") is None


class TestTimestampCacheRoundTrip:
    def test_get_last_check_returns_none_for_missing(self, tmp_path):
        cache = TimestampCache(cache_file=str(tmp_path / "ts.json"))
        assert cache.get_last_check("movie", 999) is None

    def test_update_then_get(self, tmp_path):
        cache = TimestampCache(cache_file=str(tmp_path / "ts.json"))
        before = _utcnow()
        cache.update_check("movie", 42)
        after = _utcnow()
        fetched = cache.get_last_check("movie", 42)
        assert fetched is not None
        assert fetched.tzinfo is not None
        assert before <= fetched <= after

    def test_update_persists_to_file(self, tmp_path):
        """update_check() writes to disk immediately."""
        path = tmp_path / "ts.json"
        cache = TimestampCache(cache_file=str(path))
        cache.update_check("series", 17)
        # Reload from disk
        cache2 = TimestampCache(cache_file=str(path))
        assert cache2.get_last_check("series", 17) is not None


class TestShouldRecheck:
    def test_never_checked_recommends_recheck(self, tmp_path):
        cache = TimestampCache(cache_file=str(tmp_path / "ts.json"))
        assert cache.should_recheck("movie", 1, recheck_days=30) is True

    def test_fresh_check_skips_recheck(self, tmp_path):
        cache = TimestampCache(cache_file=str(tmp_path / "ts.json"))
        cache.update_check("movie", 1)
        assert cache.should_recheck("movie", 1, recheck_days=30) is False

    def test_expired_check_recommends_recheck(self, tmp_path):
        """Past-dated timestamps trigger recheck."""
        path = tmp_path / "ts.json"
        # Manually write an old timestamp to the cache file
        old = (_utcnow() - timedelta(days=60)).isoformat()
        path.write_text(json.dumps({"movie:5": old}))
        cache = TimestampCache(cache_file=str(path))
        assert cache.should_recheck("movie", 5, recheck_days=30) is True


class TestLegacyNaiveFiles:
    """Existing cache files from before §2.9 had naive timestamps."""

    def test_reads_legacy_naive_format(self, tmp_path):
        path = tmp_path / "ts.json"
        naive = datetime(2020, 1, 1, 12, 0, 0).isoformat()  # no tzinfo
        path.write_text(json.dumps({"movie:1": naive}))
        cache = TimestampCache(cache_file=str(path))
        last = cache.get_last_check("movie", 1)
        assert last is not None
        assert last.tzinfo is not None
        # Much more than 30 days old
        assert cache.should_recheck("movie", 1, recheck_days=30) is True


class TestClearItem:
    def test_clear_item_removes_entry(self, tmp_path):
        cache = TimestampCache(cache_file=str(tmp_path / "ts.json"))
        cache.update_check("movie", 1)
        assert cache.get_last_check("movie", 1) is not None
        cache.clear_item("movie", 1)
        assert cache.get_last_check("movie", 1) is None

    def test_clear_missing_item_is_noop(self, tmp_path):
        cache = TimestampCache(cache_file=str(tmp_path / "ts.json"))
        cache.clear_item("movie", 999)  # should not raise
