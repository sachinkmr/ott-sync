#!/usr/bin/env python3
"""
OTT Hooks - Main Entry Point

OTT-aware governance for Radarr/Sonarr with modular architecture.
Supports hot reload via SIGHUP signal or config file changes.
"""

import logging
import os
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
_managers = {}  # Stores radarr, sonarr, and telegram for hot reload
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
        
        # Initialize TMDb/AniList clients for ratings (optional, but recommended for manual mode)
        tmdb_client = None
        anilist_client = None
        
        # Initialize anime detection if enabled
        anime_detector = None
        anime_config_dict = None
        if config.anime_detection and config.anime_detection.enabled:
            logger.info("🎌 Initializing anime detection...")
            from ott.clients.tmdb import TMDBClient
            from ott.clients.anilist import AniListClient
            from ott.utils.anime_detector import AnimeDetector
            
            tmdb_client = TMDBClient(
                api_key=config.anime_detection.tmdb_api_key,
                rate_limit_calls=config.anime_detection.tmdb_rate_limit_calls,
                rate_limit_period=config.anime_detection.tmdb_rate_limit_period
            )
            
            anilist_client = AniListClient(
                rate_limit_calls=config.anime_detection.anilist_rate_limit_calls,
                rate_limit_period=config.anime_detection.anilist_rate_limit_period
            )
            
            anime_detector = AnimeDetector(
                tmdb_client=tmdb_client,
                anilist_client=anilist_client,
                require_anilist_match=config.anime_detection.require_anilist_match
            )
            
            # Convert anime config to dict for manager
            anime_config_dict = {
                "detection": {
                    "require_anilist_match": config.anime_detection.require_anilist_match
                },
                "metadata": {
                    "auto_set_series_type": config.anime_detection.auto_set_series_type,
                    "auto_set_profile": config.anime_detection.auto_set_profile,
                    "profile_name": config.anime_detection.profile_name,
                    "skip_if_profile_contains_anime": config.anime_detection.skip_if_profile_contains_anime
                },
                "telegram": {
                    "notify_maybe": config.anime_detection.notify_maybe,
                    "admin_chat_id": config.telegram.get("admin_chat_id", ""),
                }
            }
            
            logger.info("  ✓ Anime detection initialized")

        # Optional OMDb client for IMDb ratings
        omdb_client = None
        if config.omdb_api_key:
            from ott.clients.omdb import OMDbClient
            omdb_client = OMDbClient(
                api_key=config.omdb_api_key,
                rate_limit_calls=config.omdb_rate_limit_calls,
                rate_limit_period=config.omdb_rate_limit_period,
            )
            logger.info("  ✓ OMDb client initialized (IMDb ratings enabled)")

        # Build the new manager set as a local dict, then swap _managers
        # atomically. In-flight handlers that already dereferenced _managers
        # keep using the old instances; subsequent lookups see the new set.
        # The assignment `_managers = new_managers` is a single bytecode op,
        # so there is no observable half-swapped state.
        manual_add_kwargs = dict(
            manual_add_detection_enabled=config.manual_add_detection_enabled,
            import_list_tags=config.import_list_tags,
            manual_add_auto_apply_override=config.manual_add_auto_apply_override,
            manual_add_tag_recheck_delay_ms=config.manual_add_tag_recheck_delay_ms,
        )
        new_managers = {
            'telegram': telegram,
            'radarr': RadarrManager(
                arr_client=radarr_client,
                justwatch_client=justwatch,
                telegram=telegram,
                ott_providers=set(config.ott_providers),
                verification_delay_seconds=config.verification_delay_seconds,
                auto_download=config.auto_download,
                tmdb_client=tmdb_client,
                anilist_client=anilist_client,
                omdb_client=omdb_client,
                **manual_add_kwargs,
            ),
            'sonarr': SonarrManager(
                arr_client=sonarr_client,
                justwatch_client=justwatch,
                telegram=telegram,
                ott_providers=set(config.ott_providers),
                verification_delay_seconds=config.verification_delay_seconds,
                auto_download=config.auto_download,
                tmdb_client=tmdb_client,
                anilist_client=anilist_client,
                omdb_client=omdb_client,
                anime_detector=anime_detector,
                anime_config=anime_config_dict,
                **manual_add_kwargs,
            ),
        }
        # Apply rating-gate config to both managers
        for mgr in (new_managers['radarr'], new_managers['sonarr']):
            mgr.rating_gate_enabled = config.rating_gate_enabled
            mgr.rating_gate_auto_download_pct = config.rating_gate_auto_download_pct
            mgr.rating_gate_approval_pct = config.rating_gate_approval_pct
            mgr.rating_gate_min_vote_count = config.rating_gate_min_vote_count
            mgr.rating_gate_trending_window = config.rating_gate_trending_window
            mgr.rating_gate_defer_days = config.rating_gate_defer_days
            mgr.rating_gate_max_defer_attempts = config.rating_gate_max_defer_attempts

        _managers = new_managers

        logger.info("✓ Configuration reloaded - new settings active for future requests")
        logger.info(f"  - OTT Providers: {len(config.ott_providers)}")
        logger.info(f"  - Telegram: {'enabled' if telegram.enabled else 'disabled'}")
        logger.info(f"  - IMDb Ratings: {'enabled (OMDb)' if omdb_client else 'disabled'}")
        logger.info(f"  - Rating Gate: {'enabled' if config.rating_gate_enabled else 'disabled'}")
        logger.info(f"  - Region: {config.region}")
        logger.info(f"  - Auto Download: {'enabled' if config.auto_download else 'disabled (manual mode)'}")
        logger.info(f"  - Anime Detection: {'enabled' if anime_detector else 'disabled'}")
        
    except Exception as e:
        logger.error(f"Configuration reload failed: {e}", exc_info=True)
        raise


def main():
    """Initialize and run OTT Hooks application"""
    global _managers, _config_path
    
    # Load configuration
    try:
        # OTT_SYNC_CONFIG_PATH takes precedence if set. Otherwise try default
        # paths in order (Docker container first, then known host layouts).
        env_path = os.environ.get("OTT_SYNC_CONFIG_PATH")
        if env_path:
            config_paths = [Path(env_path)]
        else:
            config_paths = [
                Path("/config/config.json"),
                Path("/ssd/tools/docker/arrs/ott-sync/config.json"),
                Path("/ssd/tools/docker/plex_addons/ott-sync/config.json"),
            ]

        _config_path = None
        for path in config_paths:
            if path.exists():
                _config_path = path
                config = Config.load(path)
                break

        if not _config_path:
            logger.error(
                f"Config file not found in any of: {[str(p) for p in config_paths]}. "
                "Set OTT_SYNC_CONFIG_PATH env var to an explicit path if your "
                "config lives elsewhere."
            )
            sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)
    
    # ========== Initialize Database (v2.0.0) ==========
    # Fail-fast: get_db() is called from repositories, metrics, cache invalidation,
    # override history, and webhook idempotency. A missing DB makes half the app
    # crash at the first webhook. Exit early with a clear message instead.
    logger.info("📊 Initializing database...")
    try:
        from ott.db.client import initialize_database
        db = initialize_database(
            db_path=config.database_path,
            enable_wal=config.database_enable_wal
        )
        logger.info(f"  ✓ Database initialized: {config.database_path}")

        # Run health check
        health = db.health_check()
        if health["status"] == "healthy":
            logger.info(f"  ✓ Database healthy ({health['database']['size_mb']} MB)")
        else:
            logger.warning(f"  ⚠ Database health check: {health.get('error', 'Unknown issue')}")
    except Exception as e:
        logger.error(f"Failed to initialize database at {config.database_path}: {e}", exc_info=True)
        logger.error(
            "  Database is required. Check that the path is writable, the disk "
            "has space, and no other process holds the file exclusively."
        )
        sys.exit(1)
    
    # ========== Initialize Cache (v2.0.0) ==========
    # Cache is a pure performance enhancement layered on top of the DB. When
    # it fails, register_cache_routes is simply skipped and managers fall back
    # to live JustWatch lookups - slower, but functionally complete.
    _justwatch_cache = None
    if config.cache_enabled:
        logger.info("💾 Initializing JustWatch cache...")
        try:
            from ott.utils.cache import JustWatchCache
            _justwatch_cache = JustWatchCache(
                ttl_found_days=config.cache_ttl_found_days,
                ttl_not_found_hours=config.cache_ttl_not_found_hours,
                max_size_mb=config.cache_max_size_mb,
            )
            logger.info(f"  ✓ Cache enabled (TTL: {config.cache_ttl_found_days}d found, {config.cache_ttl_not_found_hours}h not found)")
        except Exception as e:
            logger.error(f"Failed to initialize cache: {e}", exc_info=True)
            logger.warning("  ⚠ Cache disabled - every JustWatch lookup will hit the network")
            _justwatch_cache = None
    else:
        logger.info("💾 Cache disabled by configuration")
    
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
    
    # Initialize anime detection if enabled
    anime_detector = None
    anime_config_dict = None
    if config.anime_detection and config.anime_detection.enabled:
        logger.info("🎌 Initializing anime detection...")
        from ott.clients.tmdb import TMDBClient
        from ott.clients.anilist import AniListClient
        from ott.utils.anime_detector import AnimeDetector
        
        tmdb_client = TMDBClient(
            api_key=config.anime_detection.tmdb_api_key,
            rate_limit_calls=config.anime_detection.tmdb_rate_limit_calls,
            rate_limit_period=config.anime_detection.tmdb_rate_limit_period
        )
        
        anilist_client = AniListClient(
            rate_limit_calls=config.anime_detection.anilist_rate_limit_calls,
            rate_limit_period=config.anime_detection.anilist_rate_limit_period
        )
        
        anime_detector = AnimeDetector(
            tmdb_client=tmdb_client,
            anilist_client=anilist_client,
            require_anilist_match=config.anime_detection.require_anilist_match
        )
        
        # Convert anime config to dict for manager
        anime_config_dict = {
            "detection": {
                "require_anilist_match": config.anime_detection.require_anilist_match
            },
            "metadata": {
                "auto_set_series_type": config.anime_detection.auto_set_series_type,
                "auto_set_profile": config.anime_detection.auto_set_profile,
                "profile_name": config.anime_detection.profile_name,
                "skip_if_profile_contains_anime": config.anime_detection.skip_if_profile_contains_anime
            },
            "telegram": {
                "notify_maybe": config.anime_detection.notify_maybe,
                "admin_chat_id": config.telegram.get("admin_chat_id", ""),
            }
        }
        
        logger.info("  ✓ Anime detection initialized")

    # Optional OMDb client for IMDb ratings
    omdb_client = None
    if config.omdb_api_key:
        from ott.clients.omdb import OMDbClient
        omdb_client = OMDbClient(
            api_key=config.omdb_api_key,
            rate_limit_calls=config.omdb_rate_limit_calls,
            rate_limit_period=config.omdb_rate_limit_period,
        )
        logger.info("  ✓ OMDb client initialized (IMDb ratings enabled)")

    # Initialize managers with dependency injection
    logger.info("Initializing managers...")

    # Store telegram in _managers for hot reload access
    _managers['telegram'] = telegram

    manual_add_kwargs = dict(
        manual_add_detection_enabled=config.manual_add_detection_enabled,
        import_list_tags=config.import_list_tags,
        manual_add_auto_apply_override=config.manual_add_auto_apply_override,
        manual_add_tag_recheck_delay_ms=config.manual_add_tag_recheck_delay_ms,
    )
    _managers['radarr'] = RadarrManager(
        arr_client=radarr_client,
        justwatch_client=justwatch,
        telegram=telegram,
        ott_providers=set(config.ott_providers),
        verification_delay_seconds=config.verification_delay_seconds,
        auto_download=config.auto_download,
        tmdb_client=tmdb_client,
        anilist_client=anilist_client,
        omdb_client=omdb_client,
        **manual_add_kwargs,
    )

    _managers['sonarr'] = SonarrManager(
        arr_client=sonarr_client,
        justwatch_client=justwatch,
        telegram=telegram,
        ott_providers=set(config.ott_providers),
        verification_delay_seconds=config.verification_delay_seconds,
        auto_download=config.auto_download,
        tmdb_client=tmdb_client,
        anilist_client=anilist_client,
        omdb_client=omdb_client,
        anime_detector=anime_detector,
        anime_config=anime_config_dict,
        **manual_add_kwargs,
    )

    # Apply rating-gate config
    for mgr in (_managers['radarr'], _managers['sonarr']):
        mgr.rating_gate_enabled = config.rating_gate_enabled
        mgr.rating_gate_auto_download_pct = config.rating_gate_auto_download_pct
        mgr.rating_gate_approval_pct = config.rating_gate_approval_pct
        mgr.rating_gate_min_vote_count = config.rating_gate_min_vote_count
        mgr.rating_gate_trending_window = config.rating_gate_trending_window
        mgr.rating_gate_defer_days = config.rating_gate_defer_days
        mgr.rating_gate_max_defer_attempts = config.rating_gate_max_defer_attempts

    # Run anime migration if enabled
    if anime_detector and config.anime_detection.auto_run_on_startup:
        logger.info("🎌 Running anime migration on startup...")
        try:
            stats = _managers['sonarr'].migrate_all_anime()
            logger.info(f"  ✓ Anime migration complete: {stats}")
        except Exception as e:
            logger.error(f"  ✗ Anime migration failed: {e}", exc_info=True)
    
    # Register API routes (use lambda to get current managers)
    logger.info("Registering API routes...")
    register_webhook_routes(
        lambda: _managers['radarr'],
        lambda: _managers['sonarr']
    )
    register_telegram_routes(
        lambda: _managers['radarr'],
        lambda: _managers['sonarr'],
        lambda: _managers['telegram']
    )
    register_health_routes(
        lambda: _managers['radarr'],
        lambda: _managers['sonarr'],
        wakeup_mac=config.wakeup_mac_address,
        wakeup_broadcast=config.wakeup_broadcast_address,
    )
    
    # ========== Register v2.0.0 Routes ==========
    # Register cache management routes (if cache is enabled)
    if _justwatch_cache:
        from ott.api.cache import register_cache_routes
        register_cache_routes(lambda: _justwatch_cache)
        logger.info("  ✓ Cache management routes registered")
    
    # Register metrics and analytics routes
    from ott.api.metrics import register_metrics_routes
    register_metrics_routes()
    logger.info("  ✓ Metrics routes registered")
    
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
