"""Configuration management for OTT Hooks"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .exceptions import ConfigurationError

logger = logging.getLogger("ott-hooks")


class AnimeDetectionConfig:
    """Configuration for anime detection system"""
    
    def __init__(self, config_dict: dict[str, Any]):
        """Initialize anime detection configuration
        
        Args:
            config_dict: Dictionary containing anime detection configuration
        """
        self.enabled: bool = config_dict.get("enabled", False)
        
        # API configuration
        api_config = config_dict.get("api", {})
        self.tmdb_api_key: str = api_config.get("tmdb_api_key", "")
        self.tmdb_rate_limit_calls: int = api_config.get("tmdb_rate_limit", {}).get("max_calls", 40)
        self.tmdb_rate_limit_period: int = api_config.get("tmdb_rate_limit", {}).get("period_seconds", 10)
        self.anilist_enabled: bool = api_config.get("anilist_enabled", True)
        self.anilist_rate_limit_calls: int = api_config.get("anilist_rate_limit", {}).get("max_calls", 90)
        self.anilist_rate_limit_period: int = api_config.get("anilist_rate_limit", {}).get("period_seconds", 60)
        
        # Detection configuration
        detection_config = config_dict.get("detection", {})
        self.require_anilist_match: bool = detection_config.get("require_anilist_match", False)
        self.process_all_anilist_results: bool = detection_config.get("process_all_anilist_results", True)
        
        # Metadata configuration
        metadata_config = config_dict.get("metadata", {})
        self.auto_set_series_type: bool = metadata_config.get("auto_set_series_type", True)
        self.auto_set_profile: bool = metadata_config.get("auto_set_profile", True)
        self.profile_name: str = metadata_config.get("profile_name", "Anime")
        self.skip_if_profile_contains_anime: bool = metadata_config.get("skip_if_profile_contains_anime", True)
        
        # Tag configuration
        tags_config = config_dict.get("tags", {})
        self.tag_checked: str = tags_config.get("checked", "anime-checked")
        self.tag_detected: str = tags_config.get("detected", "anime-detected")
        self.tag_maybe: str = tags_config.get("maybe", "anime-maybe")
        
        # Telegram configuration
        telegram_config = config_dict.get("telegram", {})
        self.notify_maybe: bool = telegram_config.get("notify_maybe", True)
        self.notify_detected: bool = telegram_config.get("notify_detected", False)
        
        # Migration configuration
        migration_config = config_dict.get("migration", {})
        self.migration_mode: str = migration_config.get("mode", "immediate")
        self.auto_run_on_startup: bool = migration_config.get("auto_run_on_startup", True)
        self.parallel_processing: bool = migration_config.get("parallel_processing", True)
    
    def validate(self) -> None:
        """Validate anime detection configuration
        
        Raises:
            ValueError: If configuration is invalid
        """
        if self.enabled:
            if not self.tmdb_api_key:
                raise ValueError(
                    "anime_detection.api.tmdb_api_key is required when anime detection is enabled. "
                    "Get a free API key from https://www.themoviedb.org/settings/api"
                )
            
            if self.migration_mode not in ["immediate", "manual", "disabled"]:
                raise ValueError(
                    f"Invalid migration_mode: {self.migration_mode}. "
                    "Must be 'immediate', 'manual', or 'disabled'"
                )


class Config:
    """Configuration container for OTT Hooks application"""
    
    def __init__(self, config_dict: dict[str, Any]):
        """Initialize configuration from dictionary
        
        Args:
            config_dict: Dictionary containing all configuration values
        """
        # Radarr configuration
        self.radarr_url: str = config_dict["radarr_url"]
        self.radarr_api_key: str = config_dict["radarr_api_key"]
        
        # Sonarr configuration
        self.sonarr_url: str = config_dict["sonarr_url"]
        self.sonarr_api_key: str = config_dict["sonarr_api_key"]
        
        # OTT providers
        self.ott_providers: list[str] = config_dict["ott_providers"]
        
        # Telegram configuration (dict-form, consumed by TelegramNotifier)
        self.telegram: dict[str, Any] = config_dict.get("telegram", {})
        self.telegram_admin_chat_id: Optional[str] = self.telegram.get("admin_chat_id")

        # Region
        self.region: str = config_dict.get("region", "IN")
        
        # Cron configuration
        self.cron_initial_delay_seconds: int = config_dict.get("cron_initial_delay_seconds", 60)
        self.cron_interval_hours: int = config_dict.get("cron_interval_hours", 24)
        
        # Verification delay
        self.verification_delay_seconds: int = config_dict.get("verification_delay_seconds", 60)
        
        # Webhook events
        self.webhook_events: list[str] = config_dict.get("webhook_events", ["MovieAdded", "SeriesAdded"])
        
        # JustWatch rate limiting
        jw_rate_limit = config_dict.get("justwatch_rate_limit", {})
        self.justwatch_rate_limit_calls: int = jw_rate_limit.get("max_calls", 60)
        self.justwatch_rate_limit_period: int = jw_rate_limit.get("period_seconds", 60)
        
        # ========== v2.0.0 New Configuration Options (with backward compatibility) ==========
        
        # Database configuration
        db_config = config_dict.get("database", {})
        self.database_path: str = db_config.get("path", "/config/ott-hooks.db")
        self.database_enable_wal: bool = db_config.get("enable_wal", True)
        self.database_backup_enabled: bool = db_config.get("backup_enabled", False)
        self.database_backup_interval_hours: int = db_config.get("backup_interval_hours", 24)
        
        # Cache configuration
        cache_config = config_dict.get("cache", {})
        self.cache_enabled: bool = cache_config.get("enabled", True)
        self.cache_ttl_found_days: int = cache_config.get("ttl_found_days", 7)
        self.cache_ttl_not_found_hours: int = cache_config.get("ttl_not_found_hours", 24)
        self.cache_max_size_mb: int = cache_config.get("max_size_mb", 50)
        
        # Performance configuration
        perf_config = config_dict.get("performance", {})
        self.max_concurrent_webhooks: int = perf_config.get("max_concurrent_webhooks", 5)
        self.max_concurrent_justwatch_calls: int = perf_config.get("max_concurrent_justwatch_calls", 10)
        self.enable_async_processing: bool = perf_config.get("enable_async_processing", False)
        
        # Metrics configuration
        metrics_config = config_dict.get("metrics", {})
        self.metrics_retention_days: int = metrics_config.get("retention_days", 90)
        
        # Anime detection (existing, preserved)
        anime_config = config_dict.get("anime_detection")
        if anime_config:
            self.anime_detection = AnimeDetectionConfig(anime_config)
        else:
            self.anime_detection = None
        
        # Auto download mode (True = automatic, False = manual approval required)
        self.auto_download: bool = config_dict.get("auto_download", False)

        # Optional OMDb API key for IMDb ratings (free tier: 1,000 req/day).
        # When set, _fetch_ratings will include IMDb scores in Telegram captions.
        omdb_cfg = config_dict.get("omdb", {}) or {}
        self.omdb_api_key: str = omdb_cfg.get("api_key", "")
        self.omdb_rate_limit_calls: int = omdb_cfg.get("rate_limit_calls", 50)
        self.omdb_rate_limit_period: int = omdb_cfg.get("rate_limit_period", 60)

        # Optional Wake-on-LAN target for the /wakeup utility endpoint.
        # Both fields empty disables the endpoint (returns 400).
        wakeup_cfg = config_dict.get("wakeup", {}) or {}
        self.wakeup_mac_address: str = wakeup_cfg.get("mac_address", "")
        self.wakeup_broadcast_address: str = wakeup_cfg.get("broadcast_address", "")

        # Optional inverse-tag detection for manually-added items.
        # If an incoming item carries NONE of import_list_tags, treat it as a
        # manual add and apply ott-override automatically to skip OTT logic.
        # Disabled by default - users must opt in by listing their import
        # list's tag labels.
        mad_cfg = config_dict.get("manual_add_detection", {}) or {}
        self.manual_add_detection_enabled: bool = mad_cfg.get("enabled", False)
        self.import_list_tags: list[str] = mad_cfg.get("import_list_tags", []) or []
        self.manual_add_auto_apply_override: bool = mad_cfg.get(
            "auto_apply_override_tag", True,
        )
        self.manual_add_tag_recheck_delay_ms: int = mad_cfg.get(
            "tag_recheck_delay_ms", 1500,
        )

        # Store raw config for backward compatibility
        self._raw_config = config_dict
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by key (for backwards compatibility)

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        return self._raw_config.get(key, default)

    def validate(self) -> None:
        """Cross-field sanity checks on the loaded configuration.

        Catches misconfigurations that would otherwise fail at runtime
        with cryptic errors (bad URLs, zero/negative intervals, empty
        provider lists, etc.). Raises ConfigurationError with a clear
        message describing the first problem found.
        """
        errors: list[str] = []

        # URLs must be HTTP(S)
        for field_name, url in [
            ("radarr_url", self.radarr_url),
            ("sonarr_url", self.sonarr_url),
        ]:
            if not url or not isinstance(url, str):
                errors.append(f"{field_name} is required (non-empty string)")
                continue
            if not (url.startswith("http://") or url.startswith("https://")):
                errors.append(
                    f"{field_name} must start with http:// or https:// (got {url!r})"
                )

        # API keys must be non-empty
        for field_name, key in [
            ("radarr_api_key", self.radarr_api_key),
            ("sonarr_api_key", self.sonarr_api_key),
        ]:
            if not key or not isinstance(key, str):
                errors.append(f"{field_name} is required (non-empty string)")

        # Provider list non-empty
        if not self.ott_providers:
            errors.append("ott_providers must contain at least one provider")

        # Region - accept 2-letter ISO 3166-1 or anything reasonable-looking
        if not self.region or not isinstance(self.region, str) or len(self.region) < 2:
            errors.append(f"region must be a non-empty code (got {self.region!r})")

        # Positive intervals
        positive_int_fields = {
            "cron_interval_hours": self.cron_interval_hours,
            "justwatch_rate_limit_calls": self.justwatch_rate_limit_calls,
            "justwatch_rate_limit_period": self.justwatch_rate_limit_period,
            "cache_ttl_found_days": self.cache_ttl_found_days,
            "cache_ttl_not_found_hours": self.cache_ttl_not_found_hours,
            "cache_max_size_mb": self.cache_max_size_mb,
            "metrics_retention_days": self.metrics_retention_days,
            "max_concurrent_webhooks": self.max_concurrent_webhooks,
            "max_concurrent_justwatch_calls": self.max_concurrent_justwatch_calls,
        }
        for name, value in positive_int_fields.items():
            if not isinstance(value, int) or value <= 0:
                errors.append(f"{name} must be a positive integer (got {value!r})")

        # Non-negative intervals
        non_negative_fields = {
            "cron_initial_delay_seconds": self.cron_initial_delay_seconds,
            "verification_delay_seconds": self.verification_delay_seconds,
        }
        for name, value in non_negative_fields.items():
            if not isinstance(value, int) or value < 0:
                errors.append(f"{name} must be >= 0 (got {value!r})")

        # Backup interval only when backup enabled
        if self.database_backup_enabled and self.database_backup_interval_hours <= 0:
            errors.append(
                "database_backup_interval_hours must be > 0 when "
                "database_backup_enabled is true"
            )

        if errors:
            raise ConfigurationError(
                "Configuration validation failed:\n  - " + "\n  - ".join(errors)
            )
    
    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load and validate configuration from JSON file

        Args:
            path: Path to configuration JSON file (caller is responsible for
                  path discovery/fallbacks; this method trusts the path given)

        Returns:
            Config instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            json.JSONDecodeError: If config file is invalid JSON
            ConfigurationError: If required keys are missing or validation fails
        """
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        logger.info(f"Loading config from {path}")
        
        with path.open("r", encoding="utf-8") as f:
            config_dict = json.load(f)
        
        # Validate required keys
        required_keys = [
            "radarr_url", "radarr_api_key",
            "sonarr_url", "sonarr_api_key",
            "ott_providers"
        ]
        
        missing_keys = [key for key in required_keys if key not in config_dict]
        if missing_keys:
            raise ConfigurationError(f"Missing required config keys: {missing_keys}")

        # Create config instance
        config = cls(config_dict)

        # Cross-field validation
        config.validate()

        # Validate anime detection if enabled
        if config.anime_detection and config.anime_detection.enabled:
            try:
                config.anime_detection.validate()
                logger.info("✅ Anime detection enabled and validated")
            except ValueError as e:
                logger.error(f"❌ Anime detection config invalid: {e}")
                raise ConfigurationError(f"anime_detection invalid: {e}") from e

        logger.info("Config loaded successfully")
        return config
    
    @classmethod
    def load_default(cls) -> "Config":
        """Load configuration from default paths
        
        Tries paths in order:
        1. /config/config.json (Docker container)
        2. /ssd/tools/docker/plex_addons/ott-sync/config.json (Development)
        
        Returns:
            Config instance
            
        Raises:
            SystemExit: If no config file found
        """
        paths = [
            Path("/config/config.json"),
            Path("/ssd/tools/docker/plex_addons/ott-sync/config.json"),
        ]
        
        for path in paths:
            if path.exists():
                return cls.load(path)
        
        logger.error(f"Config file not found in any of: {[str(p) for p in paths]}")
        raise SystemExit(1)
