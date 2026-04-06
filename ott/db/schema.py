"""SQLAlchemy database schema for OTT Hooks"""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
    Text,
    Index,
    Float,
)
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class ProcessingMetricsModel(Base):
    """Table for storing processing metrics over time"""
    
    __tablename__ = "processing_metrics"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    service = Column(String(20), nullable=False, index=True)  # 'radarr' or 'sonarr'
    operation_type = Column(String(50), nullable=False)  # 'webhook', 'cron', 'manual'
    
    # Metrics
    checked = Column(Integer, default=0)
    cleaned = Column(Integer, default=0)
    marked_processed = Column(Integer, default=0)
    skipped_override = Column(Integer, default=0)
    already_processed = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    
    # Performance metrics
    processing_time_ms = Column(Integer, nullable=True)
    items_processed = Column(Integer, default=0)
    
    __table_args__ = (
        Index('idx_metrics_timestamp', 'timestamp'),
        Index('idx_metrics_service', 'service'),
        Index('idx_metrics_composite', 'service', 'timestamp'),
    )


class JustWatchCacheModel(Base):
    """Table for caching JustWatch provider lookups"""
    
    __tablename__ = "justwatch_cache"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Lookup keys
    tmdb_id = Column(Integer, nullable=True, index=True)
    imdb_id = Column(String(20), nullable=True, index=True)
    title = Column(String(500), nullable=False)
    year = Column(Integer, nullable=True)
    region = Column(String(5), nullable=False, default="IN")
    item_type = Column(String(20), nullable=False)  # 'movie' or 'series'
    
    # Cache data
    providers_json = Column(Text, nullable=True)  # JSON array of provider names
    found_on_ott = Column(Boolean, default=False)
    
    # Cache metadata
    cached_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    cache_hit_count = Column(Integer, default=0)
    last_accessed = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_cache_tmdb', 'tmdb_id', 'region'),
        Index('idx_cache_imdb', 'imdb_id', 'region'),
        Index('idx_cache_title', 'title', 'year', 'region'),
        Index('idx_cache_expires', 'expires_at'),
    )


class OverrideHistoryModel(Base):
    """Table for tracking user override actions"""
    
    __tablename__ = "override_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Item identification
    item_id = Column(Integer, nullable=False, index=True)
    service = Column(String(20), nullable=False)  # 'radarr' or 'sonarr'
    item_type = Column(String(20), nullable=False)  # 'movie' or 'series'
    title = Column(String(500), nullable=False)
    year = Column(Integer, nullable=True)
    
    # Action details
    user_action = Column(String(50), nullable=False)  # 'override_download', 'undo_override'
    user_id = Column(String(100), nullable=True)  # Telegram user ID
    user_name = Column(String(200), nullable=True)  # Telegram username
    
    # State tracking
    reason = Column(Text, nullable=True)  # Why override was applied
    previous_state = Column(Text, nullable=True)  # JSON of previous item state
    new_state = Column(Text, nullable=True)  # JSON of new item state
    
    # Provider context
    blocked_providers = Column(Text, nullable=True)  # JSON array of providers
    
    __table_args__ = (
        Index('idx_override_item', 'item_id', 'service'),
        Index('idx_override_timestamp', 'timestamp'),
        Index('idx_override_user', 'user_id'),
    )


class TagHistoryModel(Base):
    """Table for tracking tag changes over time"""
    
    __tablename__ = "tag_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Item identification
    item_id = Column(Integer, nullable=False, index=True)
    service = Column(String(20), nullable=False)  # 'radarr' or 'sonarr'
    item_type = Column(String(20), nullable=False)  # 'movie' or 'series'
    title = Column(String(500), nullable=False)
    
    # Tag details
    tag_name = Column(String(100), nullable=False, index=True)
    action = Column(String(20), nullable=False)  # 'added' or 'removed'
    
    # Context
    triggered_by = Column(String(50), nullable=True)  # 'webhook', 'cron', 'manual', 'override'
    
    __table_args__ = (
        Index('idx_tag_item', 'item_id', 'service'),
        Index('idx_tag_name', 'tag_name'),
        Index('idx_tag_timestamp', 'timestamp'),
    )


class WebhookLogModel(Base):
    """Table for logging webhook events and processing"""
    
    __tablename__ = "webhook_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Webhook details
    service = Column(String(20), nullable=False, index=True)  # 'radarr' or 'sonarr'
    event_type = Column(String(50), nullable=False, index=True)  # 'MovieAdded', 'SeriesAdded', etc.
    
    # Item details
    item_id = Column(Integer, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    
    # Processing
    payload_hash = Column(String(64), nullable=True, index=True)  # SHA256 hash for deduplication
    status = Column(String(20), nullable=False)  # 'success', 'error', 'skipped'
    processing_time_ms = Column(Integer, nullable=True)
    
    # Result
    action_taken = Column(String(100), nullable=True)  # 'blocked', 'allowed', 'error'
    error_message = Column(Text, nullable=True)
    
    __table_args__ = (
        Index('idx_webhook_timestamp', 'timestamp'),
        Index('idx_webhook_service', 'service', 'event_type'),
        # Composite index for the dedup query (WebhookRepository.check_duplicate)
        # which filters on (payload_hash, timestamp >= cutoff). SQLite will also
        # use this for payload_hash-only lookups via the leading column.
        Index('idx_webhook_dedup', 'payload_hash', 'timestamp'),
    )


class ErrorLogModel(Base):
    """Table for logging errors and exceptions"""
    
    __tablename__ = "error_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Error classification
    error_type = Column(String(100), nullable=False, index=True)  # Exception class name
    severity = Column(String(20), nullable=False)  # 'error', 'warning', 'critical'
    
    # Context
    service = Column(String(20), nullable=True)  # 'radarr', 'sonarr', or None
    item_id = Column(Integer, nullable=True, index=True)
    endpoint = Column(String(200), nullable=True)  # API endpoint that failed
    operation = Column(String(100), nullable=True)  # 'justwatch_lookup', 'arr_api_call', etc.
    
    # Error details
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    context_data = Column(Text, nullable=True)  # JSON with additional context
    
    # Resolution
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index('idx_error_timestamp', 'timestamp'),
        Index('idx_error_type', 'error_type'),
        Index('idx_error_severity', 'severity'),
        Index('idx_error_resolved', 'resolved'),
    )


# ---------------------------------------------------------------------------
# Rating-gate tables (Phase 7.1)
# ---------------------------------------------------------------------------

class PendingEvaluationModel(Base):
    """Items deferred because they had no trustworthy rating at decision time.

    The weekly cron sweeper re-checks these and promotes them to a terminal
    decision (AUTO_DOWNLOAD / SKIP / REQUEST_APPROVAL) once a rating appears.
    """
    __tablename__ = "pending_evaluation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    media_type = Column(String(10), nullable=False)     # 'movie' | 'tv'
    tmdb_id = Column(Integer, nullable=False)
    arr_type = Column(String(10), nullable=False)       # 'radarr' | 'sonarr'
    arr_item_id = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    next_check_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    last_reason = Column(Text, nullable=True)

    __table_args__ = (
        Index('idx_pending_eval_next', 'next_check_at'),
        Index('idx_pending_eval_item', 'arr_type', 'arr_item_id'),
    )


class PendingApprovalModel(Base):
    """Items for which a Telegram approval prompt was sent and not yet resolved.

    Used for dedup (don't re-prompt for the same item) and admin /metrics.
    No timeout sweeper — approvals wait forever per user spec.
    """
    __tablename__ = "pending_approval"

    id = Column(Integer, primary_key=True, autoincrement=True)
    media_type = Column(String(10), nullable=False)
    tmdb_id = Column(Integer, nullable=False)
    arr_type = Column(String(10), nullable=False)
    arr_item_id = Column(Integer, nullable=False)
    title = Column(String(500), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolution = Column(String(20), nullable=True)     # 'download' | 'skip'
    telegram_message_id = Column(Integer, nullable=True)
    telegram_chat_id = Column(Integer, nullable=True)
    rating_score_pct = Column(Float, nullable=True)
    trending = Column(Boolean, nullable=True)
    reason = Column(Text, nullable=False)

    __table_args__ = (
        Index('idx_pending_appr_item', 'arr_type', 'arr_item_id'),
        Index('idx_pending_appr_resolved', 'resolved_at'),
    )
