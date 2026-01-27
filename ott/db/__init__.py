"""Database package for OTT Hooks"""

from .client import DatabaseClient
from .schema import (
    Base,
    ProcessingMetricsModel,
    JustWatchCacheModel,
    OverrideHistoryModel,
    TagHistoryModel,
    WebhookLogModel,
    ErrorLogModel,
)

__all__ = [
    "DatabaseClient",
    "Base",
    "ProcessingMetricsModel",
    "JustWatchCacheModel",
    "OverrideHistoryModel",
    "TagHistoryModel",
    "WebhookLogModel",
    "ErrorLogModel",
]
