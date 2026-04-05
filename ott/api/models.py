"""Pydantic models for API requests and responses"""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, ValidationError


# ==================== Webhook Payloads ====================

class RadarrMoviePayload(BaseModel):
    """Radarr movie data structure"""
    id: int
    title: str
    year: Optional[int] = None
    tmdbId: Optional[int] = None
    imdbId: Optional[str] = None
    tags: list[int] = Field(default_factory=list)
    monitored: bool = True
    hasFile: bool = False


class RadarrWebhookPayload(BaseModel):
    """Radarr webhook payload structure"""
    eventType: Literal["MovieAdded", "Grab", "Download", "Rename", "MovieDelete"]
    movie: RadarrMoviePayload
    downloadId: Optional[str] = None
    isUpgrade: Optional[bool] = None


class SonarrSeriesPayload(BaseModel):
    """Sonarr series data structure"""
    id: int
    title: str
    year: Optional[int] = None
    tvdbId: Optional[int] = None
    tvMazeId: Optional[int] = None
    imdbId: Optional[str] = None
    tags: list[int] = Field(default_factory=list)
    monitored: bool = True


class SonarrWebhookPayload(BaseModel):
    """Sonarr webhook payload structure"""
    eventType: Literal["SeriesAdded", "Grab", "Download", "Rename", "SeriesDelete", "EpisodeFileDelete"]
    series: SonarrSeriesPayload
    downloadId: Optional[str] = None
    isUpgrade: Optional[bool] = None


class TelegramCallbackPayload(BaseModel):
    """Telegram callback query structure"""
    callback_query: dict
    message: Optional[dict] = None


class TelegramCallbackAction(BaseModel):
    """Parsed Telegram inline-keyboard callback data.

    All our callback_data strings follow 'verb:target:id' where:
      - verb is the action family (override, approve, anime)
      - target is the item_type (movie/series) or sub-action (confirm/reject)
      - id is a positive integer item/series id

    Use parse_callback_action() to get a validated instance from a raw string.
    """
    action: str = Field(min_length=1, max_length=32)
    target: str = Field(min_length=1, max_length=32)
    item_id: int = Field(ge=0)


def parse_callback_action(callback_data: str) -> Optional[TelegramCallbackAction]:
    """Parse and validate a telegram callback_data string.

    Returns a validated TelegramCallbackAction or None if the string does not
    match the 'verb:target:id' shape or the id is not a non-negative integer.
    Callers should check for None and return a user-visible error, not trust
    the shape of the input.
    """
    if not callback_data:
        return None
    parts = callback_data.split(":", 2)
    if len(parts) != 3:
        return None
    try:
        return TelegramCallbackAction(
            action=parts[0],
            target=parts[1],
            item_id=int(parts[2]),
        )
    except (ValueError, ValidationError):
        return None


# ==================== Response Models ====================

class WebhookResponse(BaseModel):
    """Standard webhook response"""
    ok: bool
    message: Optional[str] = None
    item_id: Optional[int] = None
    action: Optional[str] = None
    processing_time_ms: Optional[int] = None


class HealthResponse(BaseModel):
    """Health check response"""
    status: Literal["healthy", "unhealthy", "degraded"]
    timestamp: datetime
    version: str
    uptime_seconds: Optional[int] = None
    database: Optional[dict] = None
    services: Optional[dict] = None


class MetricsResponse(BaseModel):
    """Aggregated metrics response"""
    period_hours: int
    service: str
    total_checked: int = 0
    total_cleaned: int = 0
    total_marked_processed: int = 0
    total_skipped_override: int = 0
    total_already_processed: int = 0
    total_errors: int = 0
    total_items_processed: int = 0
    total_operations: int = 0


class CacheStatsResponse(BaseModel):
    """Cache statistics response"""
    total_entries: int = 0
    expired_entries: int = 0
    active_entries: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    hit_rate_percent: float = 0.0
    size_mb: Optional[float] = None


class ErrorLogResponse(BaseModel):
    """Error log entry response"""
    id: int
    timestamp: datetime
    error_type: str
    severity: Literal["error", "warning", "critical"]
    service: Optional[str] = None
    item_id: Optional[int] = None
    error_message: str
    operation: Optional[str] = None
    resolved: bool = False


class OverrideHistoryResponse(BaseModel):
    """Override history entry response"""
    timestamp: datetime
    item_id: int
    service: str
    title: str
    year: Optional[int] = None
    user_action: str
    user_name: Optional[str] = None
    blocked_providers: Optional[list[str]] = None


# ==================== Request Models ====================

class CacheInvalidateRequest(BaseModel):
    """Cache invalidation request"""
    tmdb_id: Optional[int] = None
    title: Optional[str] = None
    year: Optional[int] = None


class BatchWebhookRequest(BaseModel):
    """Batch webhook processing request"""
    service: Literal["radarr", "sonarr"]
    events: list[dict]
    parallel: bool = True
    max_workers: int = Field(default=5, ge=1, le=10)


class ManualProcessRequest(BaseModel):
    """Manual item processing request"""
    service: Literal["radarr", "sonarr"]
    item_id: int
    force_recheck: bool = False
