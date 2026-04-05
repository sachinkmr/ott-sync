"""Data models and structures"""

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
        imdb_rating: IMDb rating (0-10 scale, optional)
        anilist_rating: AniList rating (0-10 scale, optional)
        manual_mode: If True, shows manual approval message instead of OTT block message
    
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
            f"⏱ *Detected at:* {ts}\n\n"
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
            f"⏱ *Detected at:* {ts}\n\n"
            "⚠️ This title is already available on OTT.\n"
            "It will *not* be downloaded unless you approve.\n\n"
            "👇 Choose an action:"
        )

