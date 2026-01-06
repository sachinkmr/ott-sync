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
