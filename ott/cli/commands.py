"""CLI commands for OTT Hooks"""

import logging
import time
import threading
from typing import Callable

import typer
import uvicorn

from ..constants import (
    DEFAULT_CRON_INITIAL_DELAY_SECONDS,
    DEFAULT_CRON_INTERVAL_HOURS,
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
)

logger = logging.getLogger("ott-hooks")

app = typer.Typer()


def register_commands(get_radarr_mgr: Callable, get_sonarr_mgr: Callable, fastapi_app, config):
    """Register CLI commands
    
    Args:
        get_radarr_mgr: Callable that returns current RadarrManager instance
        get_sonarr_mgr: Callable that returns current SonarrManager instance
        fastapi_app: FastAPI app instance
        config: Config instance
    """
    
    @app.command()
    def cron():
        """Run scheduled cleanup once
        
        Checks all monitored items in Radarr and Sonarr,
        blocks items found on OTT providers.
        """
        logger.info("=" * 60)
        logger.info("Starting scheduled cron cleanup")
        logger.info("=" * 60)

        radarr_metrics = get_radarr_mgr().cron_cleanup()
        sonarr_metrics = get_sonarr_mgr().cron_cleanup()

        logger.info(f"Radarr metrics: {radarr_metrics}")
        logger.info(f"Sonarr metrics: {sonarr_metrics}")

        logger.info("Cron cleanup completed for all services")

    @app.command()
    def migrate_ott_tags(
        service: str = typer.Argument(
            ...,
            help="Service to migrate: 'radarr', 'sonarr', or 'all'"
        ),
        unmonitor: bool = typer.Option(
            False,
            "--unmonitor",
            help="Also unmonitor items found on OTT (default: False)"
        )
    ):
        """Add OTT provider tags to all existing items
        
        Scans all items in Radarr/Sonarr and adds tags for items on OTT platforms:
        - Provider tags (ott-netflix, ott-prime-video, etc.)
        - ott-skipped tag (indicates item was blocked)
        - ott-processed tag (indicates item was checked)
        
        By default, monitoring status is NOT changed (tags only).
        Use --unmonitor to also disable monitoring for items on OTT.
        
        Examples:
            # Tag items but keep monitoring status
            python main.py migrate-ott-tags radarr
            
            # Tag items AND unmonitor those on OTT
            python main.py migrate-ott-tags radarr --unmonitor
            
            # Process all services
            python main.py migrate-ott-tags all --unmonitor
        """
        logger.info("=" * 60)
        logger.info("Starting OTT provider tag migration")
        if unmonitor:
            logger.info("⚠️  UNMONITOR MODE: Items on OTT will be unmonitored")
        else:
            logger.info("📌 TAG-ONLY MODE: Monitoring status will NOT be changed")
        logger.info("=" * 60)
        
        if service.lower() in ["radarr", "all"]:
            logger.info("\n📽️ Migrating Radarr movies...")
            radarr_stats = get_radarr_mgr().migrate_ott_tags(unmonitor=unmonitor)
            logger.info(f"✅ Radarr migration complete: {radarr_stats}")
        
        if service.lower() in ["sonarr", "all"]:
            logger.info("\n📺 Migrating Sonarr series...")
            sonarr_stats = get_sonarr_mgr().migrate_ott_tags(unmonitor=unmonitor)
            logger.info(f"✅ Sonarr migration complete: {sonarr_stats}")
        
        logger.info("\n" + "=" * 60)
        logger.info("OTT provider tag migration completed!")
        logger.info("=" * 60)

    @app.command()
    def server(
        host: str = typer.Option(
            DEFAULT_SERVER_HOST,
            help="Host to bind the server to"
        ),
        port: int = typer.Option(
            DEFAULT_SERVER_PORT,
            help="Port to bind the server to"
        )
    ):
        """Start webhook server
        
        Runs FastAPI server to receive webhooks from Radarr/Sonarr
        and Telegram callbacks.
        """
        logger.info(f"Starting webhook server on {host}:{port}")
        
        # Get the module path for uvicorn reload
        # For new modular structure, point to the fastapi_app instance
        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            log_level="info"
        )

    @app.command()
    def run_all(
        host: str = typer.Option(
            DEFAULT_SERVER_HOST,
            help="Host to bind the server to"
        ),
        port: int = typer.Option(
            DEFAULT_SERVER_PORT,
            help="Port to bind the server to"
        )
    ):
        """Run combined server + cron mode (default)
        
        Starts webhook server and runs cron cleanup on schedule.
        This is the recommended production mode.
        """
        logger.info("Starting combined server + cron mode")

        def run_cron_loop():
            initial_delay = config.get(
                "cron_initial_delay_seconds",
                DEFAULT_CRON_INITIAL_DELAY_SECONDS
            )
            interval_hours = config.get(
                "cron_interval_hours",
                DEFAULT_CRON_INTERVAL_HOURS
            )

            logger.info(f"Cron starts in {initial_delay}s, interval {interval_hours}h")
            time.sleep(initial_delay)

            while True:
                try:
                    cron()
                except Exception as e:
                    logger.error(f"Cron cleanup failed: {e}", exc_info=True)
                
                time.sleep(interval_hours * 3600)

        # Start cron in background thread
        threading.Thread(target=run_cron_loop, daemon=True).start()
        
        # Start server in main thread
        logger.info(f"Starting webhook server on {host}:{port}")
        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            log_level="info"
        )
