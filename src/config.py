"""Configuration management for OTT Hooks"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("ott-hooks")


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
        
        # Cron configuration
        self.cron_initial_delay_seconds: int = config_dict.get("cron_initial_delay_seconds", 60)
        self.cron_interval_hours: int = config_dict.get("cron_interval_hours", 24)
        
        # Region for JustWatch
        self.region: str = config_dict.get("region", "IN")
        
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
        
        logger.info("Config loaded successfully")
        return cls(config_dict)
    
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
