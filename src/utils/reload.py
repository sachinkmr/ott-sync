"""Configuration hot reload utilities"""

import logging
import signal
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("ott-hooks")


class ConfigReloader:
    """Watches config file and reloads on changes or SIGHUP signal"""
    
    def __init__(self, config_path: Path, reload_callback: Callable):
        """Initialize config reloader
        
        Args:
            config_path: Path to config file
            reload_callback: Function to call when config should reload
        """
        self.config_path = config_path
        self.reload_callback = reload_callback
        self._last_modified: Optional[float] = None
        self._stop_event = threading.Event()
        self._watcher_thread: Optional[threading.Thread] = None
        
    def start(self, check_interval: int = 30):
        """Start watching config file for changes
        
        Args:
            check_interval: Seconds between file modification checks
        """
        # Set up signal handler for manual reload (SIGHUP)
        signal.signal(signal.SIGHUP, self._signal_handler)
        logger.info("Config reload: SIGHUP signal handler registered")
        logger.info(f"To reload config manually: kill -HUP <pid> or docker kill -s HUP <container>")
        
        # Start file watcher thread
        self._watcher_thread = threading.Thread(
            target=self._watch_file,
            args=(check_interval,),
            daemon=True
        )
        self._watcher_thread.start()
        logger.info(f"Config file watcher started (checking every {check_interval}s)")
    
    def stop(self):
        """Stop watching config file"""
        self._stop_event.set()
        if self._watcher_thread:
            self._watcher_thread.join(timeout=5)
    
    def _signal_handler(self, signum, frame):
        """Handle SIGHUP signal for manual reload"""
        logger.info("SIGHUP received - reloading configuration")
        self._trigger_reload()
    
    def _watch_file(self, check_interval: int):
        """Watch config file for modifications"""
        # Store initial modification time
        if self.config_path.exists():
            self._last_modified = self.config_path.stat().st_mtime
        
        while not self._stop_event.wait(check_interval):
            try:
                if not self.config_path.exists():
                    logger.warning(f"Config file not found: {self.config_path}")
                    continue
                
                current_mtime = self.config_path.stat().st_mtime
                
                if self._last_modified is not None and current_mtime > self._last_modified:
                    logger.info("Config file modified - reloading")
                    self._last_modified = current_mtime
                    self._trigger_reload()
                else:
                    self._last_modified = current_mtime
                    
            except Exception as e:
                logger.error(f"Error checking config file: {e}")
    
    def _trigger_reload(self):
        """Trigger config reload callback"""
        try:
            self.reload_callback()
            logger.info("✓ Configuration reloaded successfully")
        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}", exc_info=True)
