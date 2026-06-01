"""Data models and structures"""

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ProcessingMetrics:
    """Metrics for tracking processing operations"""
    checked: int = 0
    cleaned: int = 0
    marked_processed: int = 0
    skipped_override: int = 0
    already_processed: int = 0
    errors: int = 0


# Hard cap on the overview snippet inside Telegram captions. Telegram's
# sendPhoto caption limit is 1024 chars; with the rest of the message body
# (title, ratings, ott line, type/id/requested/timestamp, footer) taking
# ~350 chars in the typical case, an overview cap of 500 keeps us safely
# inside the limit even with long titles.
_OVERVIEW_MAX_CHARS = 500


def _strip_md_specials(s: str) -> str:
    """Remove Markdown control chars from user-controlled text.

    Telegram's legacy Markdown parser has no escape syntax — a stray `*` or
    `_` in TMDB/AniList synopses (e.g. "*spoilers*", italic emphasis) opens
    an entity the parser never finds the end of, killing the whole message
    with a 400 error. Strip the offenders rather than try to balance them.
    """
    return re.sub(r"[*_`\[\]]", "", s)


def build_telegram_caption(
    title: str,
    year: int | None,
    provider: str,
    region: str,
    item_type: str,
    item_id: int,
    requested_by: str | None = None,
    tmdb_rating: float | None = None,
    imdb_rating: float | None = None,
    anilist_rating: float | None = None,
    manual_mode: bool = False,
    overview: str | None = None,
) -> str:
    """Build a Telegram notification caption for an OTT-available item

    Args:
        title: Movie or series title
        year: Release year (optional)
        provider: OTT provider name (e.g., "Netflix", "Prime Video")
        region: Region code (e.g., "IN", "US")
        item_type: Type of item ("movie" or "series")
        item_id: Radarr/Sonarr item ID
        requested_by: Name of user who requested the item (optional)
        tmdb_rating: TMDb rating (0-10 scale, optional)
        imdb_rating: IMDb rating (0-10 scale, optional, via OMDb)
        anilist_rating: AniList rating (0-10 scale, optional)
        manual_mode: If True, shows manual approval message instead of OTT block message
        overview: Plot summary / synopsis from Sonarr/Radarr (optional). Will
            be truncated to {_OVERVIEW_MAX_CHARS} chars and Markdown-stripped.

    Returns:
        Formatted caption string with Markdown formatting
    """
    ts = datetime.now().strftime("%d %b %Y, %H:%M")

    # Build ratings section
    ratings_parts = []
    if tmdb_rating:
        ratings_parts.append(f"⭐ TMDb: {tmdb_rating:.1f}/10")
    if imdb_rating:
        ratings_parts.append(f"⭐ IMDb: {imdb_rating:.1f}/10")
    if anilist_rating:
        ratings_parts.append(f"⭐ AniList: {anilist_rating:.1f}/10")

    ratings_text = "\n".join(ratings_parts) if ratings_parts else "⭐ Ratings: N/A"

    # Optional plot section. Empty string when no overview provided so the
    # f-string just collapses to nothing without leaving stray blank lines.
    plot_section = ""
    if overview:
        clean = _strip_md_specials(overview).strip()
        if clean:
            if len(clean) > _OVERVIEW_MAX_CHARS:
                clean = clean[: _OVERVIEW_MAX_CHARS - 1].rstrip() + "…"
            plot_section = f"\n📝 _{clean}_\n"

    # Build caption based on mode
    if manual_mode:
        # Manual mode: Item needs approval (regardless of OTT)
        ott_info = f"📺 *OTT:* {provider} ({region})\n" if provider else "📺 *OTT:* Not available\n"

        return (
            f"🎬 *{title}*"
            f"{f' ({year})' if year else ''}\n\n"
            f"{ott_info}"
            f"{ratings_text}\n"
            f"📂 *Type:* {item_type.title()}\n"
            f"🆔 *ID:* {item_id}\n"
            f"👤 *Requested by:* {requested_by or 'Unknown'}\n"
            f"⏱ *Detected at:* {ts}\n"
            f"{plot_section}\n"
            "⚠️ *Manual approval required.*\n"
            "This item is unmonitored and will not download automatically.\n\n"
            "👇 Choose an action:"
        )
    else:
        # Automatic mode: OTT block
        return (
            f"🎬 *{title}*"
            f"{f' ({year})' if year else ''}\n\n"
            f"📺 *Available on:* {provider} ({region})\n"
            f"{ratings_text}\n"
            f"📂 *Type:* {item_type.title()}\n"
            f"🆔 *ID:* {item_id}\n"
            f"👤 *Requested by:* {requested_by or 'Unknown'}\n"
            f"⏱ *Detected at:* {ts}\n"
            f"{plot_section}\n"
            "⚠️ This title is already available on OTT.\n"
            "It will *not* be downloaded unless you approve.\n\n"
            "👇 Choose an action:"
        )

