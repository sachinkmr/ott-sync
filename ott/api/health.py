"""Health and utility endpoints"""

import logging
import time
from typing import Callable
from fastapi import HTTPException

from .app import app
from ..db.client import get_db

logger = logging.getLogger("ott-hooks")

# Track startup time for uptime calculation
_startup_time = time.time()


def register_health_routes(get_radarr_mgr: Callable, get_sonarr_mgr: Callable):
    """Register health check and utility endpoints
    
    Args:
        get_radarr_mgr: Callable that returns current RadarrManager instance
        get_sonarr_mgr: Callable that returns current SonarrManager instance
    """
    
    @app.get(
        "/health",
        summary="Comprehensive health check",
        description="Returns system health status including database and service connectivity",
        tags=["System"],
    )
    async def health_check():
        """Comprehensive health check
        
        Checks:
        - Database connectivity and status
        - Radarr API connectivity
        - Sonarr API connectivity
        - System uptime
        
        Returns:
            Health status with component details
        """
        try:
            from .. import __version__
            
            # Check database health
            try:
                db = get_db()
                db_health = db.health_check()
            except Exception as e:
                logger.error(f"[HEALTH] Database check failed: {e}")
                db_health = {"status": "unhealthy", "error": str(e)}
            
            # Check service connectivity
            radarr_status = "unknown"
            sonarr_status = "unknown"
            
            try:
                radarr_mgr = get_radarr_mgr()
                res = radarr_mgr.client.get("tag")
                radarr_status = "healthy" if res and res.status_code == 200 else "unhealthy"
            except Exception as e:
                logger.error(f"[HEALTH] Radarr check failed: {e}")
                radarr_status = "unhealthy"
            
            try:
                sonarr_mgr = get_sonarr_mgr()
                res = sonarr_mgr.client.get("tag")
                sonarr_status = "healthy" if res and res.status_code == 200 else "unhealthy"
            except Exception as e:
                logger.error(f"[HEALTH] Sonarr check failed: {e}")
                sonarr_status = "unhealthy"
            
            # Determine overall status
            overall_status = "healthy"
            if db_health.get("status") == "unhealthy":
                overall_status = "degraded"
            if radarr_status == "unhealthy" or sonarr_status == "unhealthy":
                overall_status = "degraded"
            
            uptime_seconds = int(time.time() - _startup_time)
            
            from datetime import datetime
            return {
                "status": overall_status,
                "timestamp": datetime.utcnow().isoformat(),
                "version": __version__,
                "uptime_seconds": uptime_seconds,
                "database": db_health,
                "services": {
                    "radarr": radarr_status,
                    "sonarr": sonarr_status,
                }
            }
            
        except Exception as e:
            logger.error(f"[HEALTH] Health check failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get(
        "/ping",
        summary="Simple ping check",
        description="Lightweight endpoint for basic connectivity check",
        tags=["System"],
    )
    async def ping():
        """Simple ping endpoint"""
        return {"ok": True}
    
    @app.get(
        "/wakeup",
        summary="Wake on LAN",
        description="Send magic packet to wake Beast PC",
        tags=["Utilities"],
    )
    async def wakeup():
        """Run the script alias wake_beast"""
        import subprocess
        try:            
            MAC = "d8:5e:d3:89:7e:ae"
            BROADCAST = "192.168.1.255"

            cmd = ["wakeonlan", "-i", BROADCAST, MAC]
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
    
    @app.get(
        "/cron",
        summary="Trigger manual cleanup",
        description="Manually trigger cron cleanup for both Radarr and Sonarr",
        tags=["Utilities"],
    )
    async def trigger_cron():
        """Manually trigger cron cleanup
        
        Runs cleanup for both Radarr and Sonarr.
        Useful for testing or manual reconciliation.
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
