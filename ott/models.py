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
    
    Returns:
        Formatted caption string with Markdown formatting
    """
    ts = datetime.now().strftime("%d %b %Y, %H:%M")
    return (
        f"🎬 *{title}*"
        f"{f' ({year})' if year else ''}\n\n"
        f"📺 *Available on:* {provider} ({region})\n"
        f"📂 *Type:* {item_type.title()}\n"
        f"🆔 *ID:* {item_id}\n"
        f"👤 *Requested by:* {requested_by or 'Unknown'}\n"
        f"⏱ *Detected at:* {ts}\n\n"
        "⚠️ This title is already available on OTT.\n"
        "It will *not* be downloaded unless you approve.\n\n"
        "👇 Choose an action:"
    )
