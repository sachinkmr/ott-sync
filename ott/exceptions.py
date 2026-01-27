"""Custom exceptions for OTT Hooks"""


class OTTHooksError(Exception):
    """Base exception for OTT Hooks"""
    pass


class JustWatchAPIError(OTTHooksError):
    """JustWatch API failures"""
    pass


class ArrAPIError(OTTHooksError):
    """Radarr/Sonarr API failures"""
    pass


class TelegramAPIError(OTTHooksError):
    """Telegram API failures"""
    pass


class DatabaseError(OTTHooksError):
    """SQLite/database issues"""
    pass


class ConfigurationError(OTTHooksError):
    """Invalid configuration"""
    pass


class CacheError(OTTHooksError):
    """Cache operation failures"""
    pass
