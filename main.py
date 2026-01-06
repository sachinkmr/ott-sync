#!/usr/bin/env python3
"""
OTT Hooks - Main Entry Point

OTT-aware governance for Radarr/Sonarr with modular architecture.
Supports hot reload via SIGHUP signal or config file changes.
"""

import logging
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ott-hooks")

# Import configuration
from ott.config import Config

# Import clients
from ott.clients.telegram import TelegramNotifier
from ott.clients.justwatch import JustWatchClient
from ott.clients.arr_client import ArrClient

# Import managers
from ott.managers.radarr import RadarrManager
from ott.managers.sonarr import SonarrManager

# Import API modules
from ott.api.app import app as fastapi_app
from ott.api.webhooks import register_webhook_routes
from ott.api.telegram import register_telegram_routes
from ott.api.health import register_health_routes

# Import CLI
from ott.cli.commands import app as cli_app, register_commands

# Import reload utilities
from ott.utils.reload import ConfigReloader

# Global state for hot reload
_managers = {}
_config_path = None


def reload_configuration():
    """Reload configuration and reinitialize components"""
    global _managers, _config_path
    
    logger.info("🔄 Reloading configuration...")
    
    try:
        # Reload config
        config = Config.load(_config_path)
        
        # Reinitialize clients
        telegram = TelegramNotifier(config.telegram)
        justwatch = JustWatchClient(
            region=config.region,
            rate_limit_calls=config.justwatch_rate_limit_calls,
            rate_limit_period=config.justwatch_rate_limit_period
        )
        
        radarr_client = ArrClient(config.radarr_url, config.radarr_api_key)
        sonarr_client = ArrClient(config.sonarr_url, config.sonarr_api_key)
        
        # Reinitialize managers
        _managers['radarr'] = RadarrManager(
            arr_client=radarr_client,
            justwatch_client=justwatch,
            telegram=telegram,
            ott_providers=set(config.ott_providers)
        )
        
        _managers['sonarr'] = SonarrManager(
            arr_client=sonarr_client,
            justwatch_client=justwatch,
            telegram=telegram,
            ott_providers=set(config.ott_providers)
        )
        
        logger.info("✓ Configuration reloaded - new settings active for future requests")
        logger.info(f"  - OTT Providers: {len(config.ott_providers)}")
        logger.info(f"  - Telegram: {'enabled' if telegram.enabled else 'disabled'}")
        logger.info(f"  - Region: {config.region}")
        
    except Exception as e:
        logger.error(f"Configuration reload failed: {e}", exc_info=True)
        raise


def main():
    """Initialize and run OTT Hooks application"""
    global _managers, _config_path
    
    # Load configuration
    try:
        config = Config.load_default()
        # Determine which config path was used
        for path in [Path("/config/config.json"), Path("/ssd/tools/docker/plex_addons/ott-sync/config.json")]:
            if path.exists():
                _config_path = path
                break
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)
    
    # Initialize clients
    logger.info("Initializing clients...")
    telegram = TelegramNotifier(config.telegram)
    justwatch = JustWatchClient(
        region=config.region,
        rate_limit_calls=config.justwatch_rate_limit_calls,
        rate_limit_period=config.justwatch_rate_limit_period
    )
    
    radarr_client = ArrClient(config.radarr_url, config.radarr_api_key)
    sonarr_client = ArrClient(config.sonarr_url, config.sonarr_api_key)
    
    # Initialize managers with dependency injection
    logger.info("Initializing managers...")
    _managers['radarr'] = RadarrManager(
        arr_client=radarr_client,
        justwatch_client=justwatch,
        telegram=telegram,
        ott_providers=set(config.ott_providers)
    )
    
    _managers['sonarr'] = SonarrManager(
        arr_client=sonarr_client,
        justwatch_client=justwatch,
        telegram=telegram,
        ott_providers=set(config.ott_providers)
    )
    
    # Register API routes (use lambda to get current managers)
    logger.info("Registering API routes...")
    register_webhook_routes(
        lambda: _managers['radarr'],
        lambda: _managers['sonarr']
    )
    register_telegram_routes(
        lambda: _managers['radarr'],
        lambda: _managers['sonarr'],
        telegram
    )
    register_health_routes(
        lambda: _managers['radarr'],
        lambda: _managers['sonarr']
    )
    
    # Register CLI commands
    logger.info("Registering CLI commands...")
    register_commands(
        lambda: _managers['radarr'],
        lambda: _managers['sonarr'],
        fastapi_app,
        config
    )
    
    # Set up config hot reload
    if _config_path:
        logger.info("Setting up configuration hot reload...")
        reloader = ConfigReloader(_config_path, reload_configuration)
        reloader.start(check_interval=30)  # Check every 30 seconds
        logger.info(f"  - Watching: {_config_path}")
        logger.info(f"  - Reload methods:")
        logger.info(f"    • File modification (auto-detected)")
        logger.info(f"    • Signal: docker kill -s HUP <container>")
        logger.info(f"    • Signal: kill -HUP <pid>")
    
    logger.info("Initialization complete!")
    logger.info("=" * 60)
    
    # Run CLI
    cli_app()


if __name__ == "__main__":
    main()
