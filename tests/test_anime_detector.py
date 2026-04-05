"""Tests for the AnimeDetector signal-scoring classifier."""

from unittest.mock import Mock

import pytest

from ott.utils.anime_detector import AnimeDetector


def _tmdb_mock(
    series_details: dict | None = None,
    genres: list[str] | None = None,
    keywords: list[str] | None = None,
    language: str | None = None,
    countries: list[str] | None = None,
) -> Mock:
    """Build a TMDBClient mock that returns the given metadata."""
    m = Mock()
    m.get_series_details.return_value = series_details or {}
    m.extract_genres.return_value = genres or []
    m.extract_keywords.return_value = keywords or []
    m.get_original_language.return_value = language
    m.get_origin_countries.return_value = countries or []
    return m


def _anilist_mock(
    search_results: list | None = None,
    best_match: dict | None = None,
    has_id: bool = False,
    ids: dict[str, int] | None = None,
) -> Mock:
    """Build an AniListClient mock."""
    m = Mock()
    m.search_anime.return_value = search_results or []
    m.get_best_match.return_value = best_match
    m.has_anime_id.return_value = has_id
    m.extract_ids.return_value = ids or {}
    return m


class TestDefiniteAnime:
    def test_external_ids_alone_classify_as_definite(self):
        tmdb = _tmdb_mock()
        anilist = _anilist_mock(
            search_results=[{"id": 1}],
            best_match={"id": 1},
            has_id=True,
            ids={"anilist": 1},
        )
        det = AnimeDetector(tmdb, anilist)
        classification, reason, signals = det.detect("Naruto", 2002, tmdb_id=1)
        assert classification == "DEFINITE_ANIME"
        assert signals["total_score"] >= 100


class TestLikelyAnime:
    def test_japanese_animation_no_anilist(self):
        """Animation+JA language (70) alone lands in LIKELY_ANIME band."""
        tmdb = _tmdb_mock(
            series_details={"id": 1},
            genres=["animation"],
            language="ja",
            countries=["JP"],
        )
        anilist = _anilist_mock()
        det = AnimeDetector(tmdb, anilist)
        classification, _, signals = det.detect("Attack on Titan", 2013, tmdb_id=1)
        assert classification == "LIKELY_ANIME"
        assert 60 <= signals["total_score"] < 100

    def test_animation_plus_asian_country_no_language(self):
        """Animation + JP country fallback (60) lands in LIKELY_ANIME."""
        tmdb = _tmdb_mock(
            series_details={"id": 1},
            genres=["animation"],
            language=None,
            countries=["JP"],
        )
        anilist = _anilist_mock()
        det = AnimeDetector(tmdb, anilist)
        classification, _, _ = det.detect("Unknown", 2020, tmdb_id=1)
        assert classification == "LIKELY_ANIME"


class TestMaybeAnime:
    def test_anime_keywords_alone(self):
        """Keywords (50) → MAYBE_ANIME band."""
        tmdb = _tmdb_mock(
            series_details={"id": 1},
            keywords=["anime", "shounen"],
        )
        anilist = _anilist_mock()
        det = AnimeDetector(tmdb, anilist)
        classification, _, signals = det.detect("X", 2020, tmdb_id=1)
        assert classification == "MAYBE_ANIME"
        assert 40 <= signals["total_score"] < 60

    def test_anime_genre_unverified_origin(self):
        """Anime genre without Asian language/country scores 50, lands MAYBE."""
        tmdb = _tmdb_mock(
            series_details={"id": 1},
            genres=["anime"],
            language="en",
            countries=["US"],
        )
        anilist = _anilist_mock()
        det = AnimeDetector(tmdb, anilist)
        classification, _, _ = det.detect("X", 2020, tmdb_id=1)
        assert classification == "MAYBE_ANIME"


class TestNotAnime:
    def test_no_signals(self):
        tmdb = _tmdb_mock(series_details={"id": 1}, genres=["drama"], language="en")
        anilist = _anilist_mock()
        det = AnimeDetector(tmdb, anilist)
        classification, _, _ = det.detect("Breaking Bad", 2008, tmdb_id=1)
        assert classification == "NOT_ANIME"

    def test_documentary_exclusion_shortcircuits(self):
        """documentary (without animation) immediately returns NOT_ANIME."""
        tmdb = _tmdb_mock(
            series_details={"id": 1},
            genres=["documentary"],
            language="ja",  # even with signals that would score otherwise
            countries=["JP"],
        )
        anilist = _anilist_mock()
        det = AnimeDetector(tmdb, anilist)
        classification, reason, _ = det.detect("NHK Doc", 2020, tmdb_id=1)
        assert classification == "NOT_ANIME"
        assert "Documentary" in reason

    def test_animation_documentary_not_excluded(self):
        """documentary + animation is fine (e.g. animated documentaries)."""
        tmdb = _tmdb_mock(
            series_details={"id": 1},
            genres=["animation", "documentary"],
            language="ja",
            countries=["JP"],
        )
        anilist = _anilist_mock()
        det = AnimeDetector(tmdb, anilist)
        classification, _, _ = det.detect("Animated Doc", 2020, tmdb_id=1)
        # Animation+JA scores 70 → LIKELY_ANIME
        assert classification == "LIKELY_ANIME"


class TestRequireAnilistMatch:
    def test_strict_mode_without_match_is_not_anime(self):
        """require_anilist_match=True + no match → NOT_ANIME, regardless of other signals."""
        tmdb = _tmdb_mock(
            series_details={"id": 1},
            genres=["animation"],
            language="ja",  # Would normally score 70
        )
        anilist = _anilist_mock(search_results=[], best_match=None)
        det = AnimeDetector(tmdb, anilist, require_anilist_match=True)
        classification, reason, _ = det.detect("X", 2020, tmdb_id=1)
        assert classification == "NOT_ANIME"
        assert "AniList match" in reason or "strict mode" in reason.lower()

    def test_strict_mode_with_match_allows_scoring(self):
        """require_anilist_match=True + match found → normal scoring applies."""
        tmdb = _tmdb_mock(
            series_details={"id": 1},
            genres=["animation"],
            language="ja",
        )
        anilist = _anilist_mock(
            search_results=[{"id": 1}],
            best_match={"id": 1},
            has_id=True,
            ids={"anilist": 1},
        )
        det = AnimeDetector(tmdb, anilist, require_anilist_match=True)
        classification, _, _ = det.detect("X", 2020, tmdb_id=1)
        assert classification == "DEFINITE_ANIME"


class TestMissingMetadata:
    def test_no_tmdb_id_still_runs(self):
        """Without tmdb_id, detector skips TMDB lookup but AniList still runs."""
        tmdb = _tmdb_mock()
        anilist = _anilist_mock(
            search_results=[{"id": 1}],
            best_match={"id": 1},
            has_id=True,
            ids={"anilist": 1},
        )
        det = AnimeDetector(tmdb, anilist)
        classification, _, _ = det.detect("Naruto", 2002, tmdb_id=None)
        # No TMDB lookup (get_series_details not called with tmdb_id)
        assert classification == "DEFINITE_ANIME"

    def test_tmdb_fetch_failure_sets_error_flag(self):
        tmdb = Mock()
        tmdb.get_series_details.return_value = None  # simulated API failure
        anilist = _anilist_mock()
        det = AnimeDetector(tmdb, anilist)
        _, _, signals = det.detect("Unknown", 2020, tmdb_id=999)
        assert signals["details"].get("tmdb_error") is True
