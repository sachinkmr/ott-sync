"""Health and utility endpoints"""

import logging
from typing import Callable

from .app import app

logger = logging.getLogger("ott-hooks")


def register_health_routes(get_radarr_mgr: Callable, get_sonarr_mgr: Callable):
    """Register health check and utility endpoints
    
    Args:
        get_radarr_mgr: Callable that returns current RadarrManager instance
        get_sonarr_mgr: Callable that returns current SonarrManager instance
    """
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint
        
        Returns:
            Status OK
        """
        return {"status": "ok"}
    
    @app.get("/wakeup")
    async def wakeup():
        """Run the script alias wake_beast 
        
        Returns:
            Status OK
        """
        import subprocess
        try:            
            MAC = "d8:5e:d3:89:7e:ae"
            BROADCAST = "192.168.1.255"

            cmd = [
                "wakeonlan",
                "-i", BROADCAST,
                MAC
            ]
            logger.info("⚡ Waking Beast PC...")
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info("✅ Magic packet sent")
            return {"status": "ok", "message": "Magic packet sent successfully"}
        except FileNotFoundError:
            logger.error("wakeonlan command not found - install wakeonlan package")
            return {
                "status": "error", 
                "message": "wakeonlan command not found. Install with: apt-get install wakeonlan"
            }
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to send wake-on-LAN packet: {e.stderr}")
            return {"status": "error", "message": f"Failed to send packet: {e.stderr}"}
    
    @app.get("/cron")
    async def trigger_cron():
        """Manually trigger cron cleanup
        
        Runs cleanup for both Radarr and Sonarr.
        Useful for testing or manual reconciliation.
        
        Returns:
            Metrics from both services
        """
        logger.info("[API] Manual cron trigger requested")
        
        radarr_metrics = get_radarr_mgr().cron_cleanup()
        sonarr_metrics = get_sonarr_mgr().cron_cleanup()
        
        return {
            "ok": True,
            "radarr": {
                "checked": radarr_metrics.checked,
                "cleaned": radarr_metrics.cleaned,
                "marked_processed": radarr_metrics.marked_processed,
                "skipped_override": radarr_metrics.skipped_override,
                "already_processed": radarr_metrics.already_processed,
                "errors": radarr_metrics.errors,
            },
            "sonarr": {
                "checked": sonarr_metrics.checked,
                "cleaned": sonarr_metrics.cleaned,
                "marked_processed": sonarr_metrics.marked_processed,
                "skipped_override": sonarr_metrics.skipped_override,
                "already_processed": sonarr_metrics.already_processed,
                "errors": sonarr_metrics.errors,
            }
        }
