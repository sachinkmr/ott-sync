"""Tests for the rating-gate decision function (Plan.md §7.1)."""

import pytest

from ott.managers.decision import (
    Decision,
    DecisionResult,
    RatingResult,
    decide,
)


def _r(score_pct: float, vote_count: int = 200) -> RatingResult:
    return RatingResult(score_pct=score_pct, source="tmdb", vote_count=vote_count)


# ---------------------------------------------------------------------------
# Master-switch paths
# ---------------------------------------------------------------------------

class TestMasterSwitch:
    def test_manual_mode_always_asks(self):
        """auto_download=False → REQUEST_APPROVAL regardless of rating."""
        result = decide(
            auto_download=False,
            rating_gate_enabled=True,
            rating=_r(95),
            trending=True,
        )
        assert result.decision == Decision.REQUEST_APPROVAL
        assert "manual mode" in result.reason.lower()

    def test_gate_disabled_always_downloads(self):
        """auto_download=True + gate disabled → AUTO_DOWNLOAD."""
        result = decide(
            auto_download=True,
            rating_gate_enabled=False,
            rating=_r(10),
            trending=False,
        )
        assert result.decision == Decision.AUTO_DOWNLOAD
        assert "gate disabled" in result.reason.lower()


# ---------------------------------------------------------------------------
# Decision matrix (auto_download=True, rating_gate_enabled=True)
# ---------------------------------------------------------------------------

class TestAutoDownloadBand:
    def test_at_threshold(self):
        """score == 80 → AUTO_DOWNLOAD."""
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(80), trending=False,
        )
        assert r.decision == Decision.AUTO_DOWNLOAD

    def test_above_threshold(self):
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(92), trending=False,
        )
        assert r.decision == Decision.AUTO_DOWNLOAD

    def test_custom_threshold(self):
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(75), trending=False,
            auto_download_threshold_pct=75,
        )
        assert r.decision == Decision.AUTO_DOWNLOAD


class TestApprovalBand:
    def test_at_approval_threshold(self):
        """score == 70 → REQUEST_APPROVAL."""
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(70), trending=False,
        )
        assert r.decision == Decision.REQUEST_APPROVAL

    def test_mid_band(self):
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(75), trending=False,
        )
        assert r.decision == Decision.REQUEST_APPROVAL


class TestSkipLowRating:
    def test_below_approval_not_trending(self):
        """score < 70, not trending → SKIP_LOW_RATING."""
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(65), trending=False,
        )
        assert r.decision == Decision.SKIP_LOW_RATING

    def test_very_low(self):
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(10), trending=False,
        )
        assert r.decision == Decision.SKIP_LOW_RATING


class TestTrendingOverride:
    def test_low_rating_but_trending_asks(self):
        """score < 70, trending=True → REQUEST_APPROVAL."""
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(50), trending=True,
        )
        assert r.decision == Decision.REQUEST_APPROVAL
        assert "trending" in r.reason.lower()

    def test_trending_does_not_override_auto_download_band(self):
        """score >= 80 + trending → still AUTO_DOWNLOAD (not downgraded to ask)."""
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(90), trending=True,
        )
        assert r.decision == Decision.AUTO_DOWNLOAD


class TestDeferNoRating:
    def test_none_rating_defers(self):
        """No rating → DEFER_NO_RATING."""
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=None, trending=False,
        )
        assert r.decision == Decision.DEFER_NO_RATING
        assert r.rating is None

    def test_none_rating_trending_still_defers(self):
        """No rating, but trending → still defers (we don't know the score)."""
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=None, trending=True,
        )
        assert r.decision == Decision.DEFER_NO_RATING


# ---------------------------------------------------------------------------
# Result metadata
# ---------------------------------------------------------------------------

class TestResultMetadata:
    def test_result_carries_rating_and_trending(self):
        rating = _r(75)
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=rating, trending=True,
        )
        assert r.rating is rating
        assert r.trending is True

    def test_auto_download_reason_includes_pct(self):
        r = decide(
            auto_download=True, rating_gate_enabled=True,
            rating=_r(85), trending=False,
        )
        assert "85%" in r.reason
