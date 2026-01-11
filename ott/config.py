"""Configuration management for OTT Hooks"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

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
        
        # Telegram configuration
        self.telegram: dict[str, Any] = config_dict.get("telegram", {})
        self.telegram_admin_chat_id: Optional[str] = self.telegram.get("admin_chat_id")
        
        # Cron configuration
        self.cron_initial_delay_seconds: int = config_dict.get("cron_initial_delay_seconds", 60)
        self.cron_interval_hours: int = config_dict.get("cron_interval_hours", 24)
        
        # Region for JustWatch
        self.region: str = config_dict.get("region", "IN")
        
        # JustWatch rate limiting
        rate_limit_config = config_dict.get("justwatch_rate_limit", {})
        self.justwatch_rate_limit_calls: int = rate_limit_config.get("max_calls", 60)
        self.justwatch_rate_limit_period: int = rate_limit_config.get("period_seconds", 60)
        
        # Delayed verification to catch race conditions (seconds)
        self.verification_delay_seconds: int = config_dict.get("verification_delay_seconds", 60)
        
        # Anime detection configuration
        anime_config_dict = config_dict.get("anime_detection", {})
        self.anime_detection: Optional[AnimeDetectionConfig] = None
        if anime_config_dict:
            self.anime_detection = AnimeDetectionConfig(anime_config_dict)
        
        # Store full config for compatibility
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
    
    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load and validate configuration from JSON file
        
        Args:
            path: Path to configuration JSON file
            
        Returns:
            Config instance
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            json.JSONDecodeError: If config file is invalid JSON
            KeyError: If required config keys are missing
        """
        if not path.exists():
            path = Path("/ssd/tools/docker/arrs/ott-sync/config.json")
       
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
            raise KeyError(f"Missing required config keys: {missing_keys}")
        
        # Create config instance
        config = cls(config_dict)
        
        # Validate anime detection if enabled
        if config.anime_detection and config.anime_detection.enabled:
            try:
                config.anime_detection.validate()
                logger.info("✅ Anime detection enabled and validated")
            except ValueError as e:
                logger.error(f"❌ Anime detection config invalid: {e}")
                raise
        
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
