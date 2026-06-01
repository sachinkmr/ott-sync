"""CLI commands for OTT Hooks"""

import logging
import time
import threading
from typing import Callable, Optional

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


def register_commands(
    get_radarr_mgr: Callable,
    get_sonarr_mgr: Callable,
    fastapi_app,
    config,
    get_housekeeping_mgr: Optional[Callable] = None,
    get_dead_media_mgr: Optional[Callable] = None,
):
    """Register CLI commands.

    Args:
        get_radarr_mgr: Callable that returns the current RadarrManager.
        get_sonarr_mgr: Callable that returns the current SonarrManager.
        fastapi_app: FastAPI app instance.
        config: Config instance.
        get_housekeeping_mgr: Optional callable returning the current
            TorrentHousekeepingManager. When set (i.e. torrent_housekeeping
            is enabled in config), `run_all` spawns a second cron loop that
            calls `.run()` on it every `torrent_housekeeping.interval_minutes`.
        get_dead_media_mgr: Optional callable returning the current
            DeadMediaManager. When set (dead_media enabled in config),
            `run_all` spawns a third cron loop that scans qBittorrent for
            stuck torrents and sends Telegram alerts. Runs slower than
            housekeeping (default 30min vs 10min) because each alert is
            human-actionable, not a no-op.
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
    def reset_tags(
        service: str = typer.Argument(
            ...,
            help="Service to reset: 'radarr', 'sonarr', or 'all'"
        ),
        keep_override: bool = typer.Option(
            True,
            "--keep-override/--clear-override",
            help="Keep ott-override tags (default: keep them)"
        ),
        remonitor: bool = typer.Option(
            False,
            "--remonitor",
            help="Also re-monitor all unmonitored items (so cron reprocesses them)"
        ),
    ):
        """Strip all OTT tags from every item for a clean reprocess.

        Removes: ott-processed, ott-skipped, ott-low-rating, ott-pending-rating,
        ott-pending-approval, and all ott-<provider> tags. By default keeps
        ott-override (user's explicit exemptions). Also clears the
        pending_evaluation and pending_approval DB tables.

        After running this, use 'python main.py cron' to reprocess the library.
        """
        logger.info("=" * 60)
        logger.info("Resetting OTT tags for clean reprocess")
        if keep_override:
            logger.info("  Keeping ott-override tags intact")
        else:
            logger.info("  ⚠️  Also clearing ott-override tags")
        logger.info("=" * 60)

        protected = {"ott-override"} if keep_override else set()

        def _reset_service(mgr, svc_name: str) -> dict:
            items = mgr.fetch_items()
            # Fetch all tags to resolve ott-* tag IDs
            res = mgr.client.get("tag")
            if not res:
                logger.error(f"[RESET] Failed to fetch tags from {svc_name}")
                return {"error": "tag fetch failed"}
            all_tags = {t["id"]: t["label"] for t in res.json()}
            ott_tag_ids = {
                tid for tid, label in all_tags.items()
                if label.startswith("ott-") and label not in protected
            }
            # Also include anime-detection state tags if they look like ott workflow
            # (but NOT anime-checked/detected/maybe — those are anime-detection state)

            stats = {"total": len(items), "reset": 0, "skipped": 0, "remonitored": 0}
            for item in items:
                item_id = item.get("id")
                item_tags = set(item.get("tags", []))
                tags_to_remove = item_tags & ott_tag_ids
                needs_remonitor = remonitor and not item.get("monitored", True)
                if not tags_to_remove and not needs_remonitor:
                    stats["skipped"] += 1
                    continue
                new_tags = item_tags - tags_to_remove
                item["tags"] = list(new_tags)
                if needs_remonitor:
                    item["monitored"] = True
                    stats["remonitored"] += 1
                update_res = mgr.client.put(f"{mgr.item_type()}/{item_id}", json=item)
                if update_res:
                    removed = [all_tags[t] for t in tags_to_remove] if tags_to_remove else []
                    parts = []
                    if removed:
                        parts.append(f"removed {removed}")
                    if needs_remonitor:
                        parts.append("re-monitored")
                    logger.info(f"[RESET] {item.get('title')}: {', '.join(parts)}")
                    stats["reset"] += 1
                else:
                    logger.error(f"[RESET] Failed to update {item.get('title')}")
            return stats

        if service.lower() in ["radarr", "all"]:
            logger.info("\n📽️ Resetting Radarr movies...")
            radarr_stats = _reset_service(get_radarr_mgr(), "radarr")
            logger.info(f"✅ Radarr reset: {radarr_stats}")

        if service.lower() in ["sonarr", "all"]:
            logger.info("\n📺 Resetting Sonarr series...")
            sonarr_stats = _reset_service(get_sonarr_mgr(), "sonarr")
            logger.info(f"✅ Sonarr reset: {sonarr_stats}")

        # Clear rating-gate DB tables
        try:
            from ott.db.repositories.rating_gate import (
                PendingEvaluationRepository,
                PendingApprovalRepository,
            )
            from ott.db.client import get_db
            db = get_db()
            with db.session() as session:
                from ott.db.schema import PendingEvaluationModel, PendingApprovalModel
                eval_count = session.query(PendingEvaluationModel).delete()
                appr_count = session.query(PendingApprovalModel).delete()
                session.commit()
                logger.info(f"✅ Cleared DB: {eval_count} pending evaluations, {appr_count} pending approvals")
        except Exception as e:
            logger.warning(f"⚠️ DB cleanup failed (non-fatal): {e}")

        logger.info("\n" + "=" * 60)
        logger.info("Tag reset complete! Run 'python main.py cron' to reprocess.")
        logger.info("=" * 60)

    @app.command()
    def set_provider(
        title: str = typer.Argument(..., help="Title of the movie or series"),
        providers: str = typer.Argument(..., help="Comma-separated provider names (e.g. 'JioHotstar,Netflix')"),
        media_type: str = typer.Option("tv", help="'movie' or 'tv'"),
        year: Optional[int] = typer.Option(None, help="Release year"),
        tmdb_id: Optional[int] = typer.Option(None, help="TMDB ID for precise cache key"),
    ):
        """Manually set OTT provider availability for a title.

        Inserts a cache entry so subsequent lookups return these providers
        without hitting TMDB or JustWatch. Useful for titles where the APIs
        have data gaps (e.g. The Simpsons on JioHotstar in India).

        The entry follows the same 30-day TTL as API-sourced cache entries,
        after which the APIs will re-check and either confirm or update.

        Examples:
            python main.py set-provider "The Simpsons" "JioHotstar" --media-type tv --year 1989 --tmdb-id 456
            python main.py set-provider "Some Movie" "Netflix,Amazon Prime Video" --media-type movie --year 2024
        """
        provider_list = [p.strip() for p in providers.split(",") if p.strip()]
        if not provider_list:
            logger.error("No providers specified")
            raise typer.Exit(1)

        ott_client = get_radarr_mgr().justwatch
        ok = ott_client.set_manual(
            title=title, year=year, providers=provider_list,
            media_type=media_type, tmdb_id=tmdb_id,
        )
        if ok:
            logger.info(f"✅ Set '{title}' → {provider_list}")
        else:
            logger.error(f"Failed to set provider for '{title}'")
            raise typer.Exit(1)

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

        # ========== Torrent housekeeping cron (separate from OTT cron) ==========
        # Runs much more frequently (every N minutes vs. N hours for OTT).
        # Owns: resume stalled torrents + delete completed-and-hardlinked.
        # Skipped entirely when get_housekeeping_mgr is None (housekeeping
        # disabled in config).
        if get_housekeeping_mgr is not None:
            def run_housekeeping_loop():
                # Match the OTT cron's `initial_delay` so we don't immediately
                # hit qBittorrent before clients/network are settled.
                initial_delay = config.get(
                    "cron_initial_delay_seconds",
                    DEFAULT_CRON_INITIAL_DELAY_SECONDS,
                )
                interval_minutes = config.torrent_housekeeping_interval_minutes
                logger.info(
                    f"Torrent housekeeping starts in {initial_delay}s, "
                    f"interval {interval_minutes}min"
                )
                time.sleep(initial_delay)

                while True:
                    try:
                        mgr = get_housekeeping_mgr()
                        if mgr is None:
                            # Reload swapped it out (e.g. user toggled disabled).
                            # Sleep a tick and recheck.
                            time.sleep(interval_minutes * 60)
                            continue
                        result = mgr.run()
                        # `result` is a HousekeepingResult dataclass — useful
                        # counts are already logged inside .run(), so we don't
                        # duplicate here.
                        _ = result
                    except Exception as e:
                        logger.error(
                            f"Torrent housekeeping failed: {e}", exc_info=True,
                        )
                    time.sleep(interval_minutes * 60)

            threading.Thread(target=run_housekeeping_loop, daemon=True).start()
            logger.info("  ✓ Torrent housekeeping cron registered")

        # ========== Dead-media cron (separate from housekeeping) ==========
        # Slower cadence than housekeeping (default 30min vs 10min) because
        # alerts go to a human — over-frequent polling just adds latency, not
        # value. Skipped entirely when dead_media is disabled in config.
        if get_dead_media_mgr is not None:
            def run_dead_media_loop():
                # Match the housekeeping initial-delay pattern so neither
                # cron hits qBittorrent the instant the container starts.
                initial_delay = config.get(
                    "cron_initial_delay_seconds",
                    DEFAULT_CRON_INITIAL_DELAY_SECONDS,
                )
                interval_minutes = config.dead_media_poll_interval_minutes
                logger.info(
                    f"Dead-media cron starts in {initial_delay}s, "
                    f"interval {interval_minutes}min"
                )
                time.sleep(initial_delay)

                while True:
                    try:
                        mgr = get_dead_media_mgr()
                        if mgr is None:
                            # Disabled by hot-reload; wait one cycle and recheck.
                            time.sleep(interval_minutes * 60)
                            continue
                        result = mgr.run()
                        # Result counts are logged inside .run(); just stash.
                        _ = result
                    except Exception as e:
                        logger.error(
                            f"Dead-media scan failed: {e}", exc_info=True,
                        )
                    time.sleep(interval_minutes * 60)

            threading.Thread(target=run_dead_media_loop, daemon=True).start()
            logger.info("  ✓ Dead-media cron registered")

        # Start server in main thread
        logger.info(f"Starting webhook server on {host}:{port}")
        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            log_level="info"
        )
