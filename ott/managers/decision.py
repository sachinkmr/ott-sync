"""Rating-gate decision function.

Pure function — no I/O, no side effects, fully unit-testable. The caller
(base manager) is responsible for fetching the rating and trending data
and for applying the returned decision.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Decision(Enum):
    AUTO_DOWNLOAD = "auto_download"
    REQUEST_APPROVAL = "request_approval"
    SKIP_LOW_RATING = "skip_low_rating"
    DEFER_NO_RATING = "defer_no_rating"


@dataclass(frozen=True)
class RatingResult:
    """Normalized rating from any source."""
    score_pct: float       # 0-100
    source: str            # "tmdb"
    vote_count: int


@dataclass(frozen=True)
class DecisionResult:
    """Output of decide()."""
    decision: Decision
    reason: str            # human-readable, surfaced in logs + Telegram
    rating: Optional[RatingResult]
    trending: bool


def decide(
    *,
    auto_download: bool,
    rating_gate_enabled: bool,
    rating: Optional[RatingResult],
    trending: bool,
    auto_download_threshold_pct: int = 80,
    approval_threshold_pct: int = 70,
) -> DecisionResult:
    """Evaluate the rating-gate decision matrix.

    Called from the base manager after the OTT lookup returns "not found".

    Decision matrix (when auto_download=True AND rating_gate_enabled=True):

    | Rating           | Trending? | Decision            |
    |------------------|-----------|---------------------|
    | >= auto_download | —         | AUTO_DOWNLOAD       |
    | >= approval      | —         | REQUEST_APPROVAL    |
    | < approval       | Yes       | REQUEST_APPROVAL    |
    | < approval       | No        | SKIP_LOW_RATING     |
    | None (no data)   | —         | DEFER_NO_RATING     |

    When auto_download=False (manual mode), every item goes through
    REQUEST_APPROVAL regardless of rating/trending.

    When rating_gate_enabled=False (but auto_download=True), every item
    is AUTO_DOWNLOAD — the original behavior before the rating gate was
    introduced.

    Args:
        auto_download: Master switch from config.
        rating_gate_enabled: Whether the threshold gate is active.
        rating: Normalized rating result, or None when unavailable / below
            min_vote_count.
        trending: Whether the title is on the TMDB trending list.
        auto_download_threshold_pct: Score at or above which the download
            proceeds without prompting (default 80).
        approval_threshold_pct: Score at or above which the user is prompted
            (default 70). Must be <= auto_download_threshold_pct.

    Returns:
        DecisionResult describing what the caller should do.
    """
    # Master switch: manual mode → always ask
    if not auto_download:
        return DecisionResult(
            Decision.REQUEST_APPROVAL,
            "auto_download disabled (manual mode)",
            rating,
            trending,
        )

    # Gate disabled → original behavior: just download
    if not rating_gate_enabled:
        return DecisionResult(
            Decision.AUTO_DOWNLOAD,
            "rating gate disabled",
            rating,
            trending,
        )

    # No trustworthy rating → defer
    if rating is None:
        return DecisionResult(
            Decision.DEFER_NO_RATING,
            "no trustworthy rating available yet",
            None,
            trending,
        )

    # High rating → auto-download
    if rating.score_pct >= auto_download_threshold_pct:
        return DecisionResult(
            Decision.AUTO_DOWNLOAD,
            f"rating {rating.score_pct:.0f}% >= {auto_download_threshold_pct}%",
            rating,
            trending,
        )

    # Medium rating → ask
    if rating.score_pct >= approval_threshold_pct:
        return DecisionResult(
            Decision.REQUEST_APPROVAL,
            f"rating {rating.score_pct:.0f}% in review band "
            f"({approval_threshold_pct}-{auto_download_threshold_pct}%)",
            rating,
            trending,
        )

    # Low rating but trending → ask anyway
    if trending:
        return DecisionResult(
            Decision.REQUEST_APPROVAL,
            f"trending (rating {rating.score_pct:.0f}% below "
            f"{approval_threshold_pct}%)",
            rating,
            trending,
        )

    # Low rating, not trending → skip silently
    return DecisionResult(
        Decision.SKIP_LOW_RATING,
        f"rating {rating.score_pct:.0f}% < {approval_threshold_pct}% "
        f"and not trending",
        rating,
        trending,
    )
