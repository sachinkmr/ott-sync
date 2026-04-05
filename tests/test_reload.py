"""Tests for ConfigReloader file-watching and signal handling."""

import os
import threading
import time
from unittest.mock import Mock

import pytest

from ott.utils.reload import ConfigReloader


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"initial": true}')
    return path


class TestFileWatching:
    def test_reload_fires_when_file_modified(self, config_file):
        callback = Mock()
        reloader = ConfigReloader(config_file, callback)
        # Short interval so the test finishes quickly
        reloader.start(check_interval=0.1)
        try:
            # Give the watcher time to record initial mtime
            time.sleep(0.15)
            assert not callback.called

            # Force a distinct mtime (filesystems can coalesce writes within ~1s)
            future = time.time() + 2
            os.utime(config_file, (future, future))

            # Wait for watcher to notice
            deadline = time.time() + 2.0
            while time.time() < deadline and not callback.called:
                time.sleep(0.05)
            assert callback.called
        finally:
            reloader.stop()

    def test_no_reload_when_file_unchanged(self, config_file):
        callback = Mock()
        reloader = ConfigReloader(config_file, callback)
        reloader.start(check_interval=0.1)
        try:
            time.sleep(0.4)  # several check cycles
            assert not callback.called
        finally:
            reloader.stop()

    def test_stop_joins_watcher_thread(self, config_file):
        reloader = ConfigReloader(config_file, Mock())
        reloader.start(check_interval=0.1)
        assert reloader._watcher_thread is not None
        assert reloader._watcher_thread.is_alive()
        reloader.stop()
        assert not reloader._watcher_thread.is_alive()

    def test_missing_config_file_logs_but_keeps_watching(self, config_file):
        """Deleted config file is logged as a warning; watcher keeps running."""
        callback = Mock()
        reloader = ConfigReloader(config_file, callback)
        reloader.start(check_interval=0.1)
        try:
            config_file.unlink()
            time.sleep(0.3)  # watcher sees missing file
            assert reloader._watcher_thread.is_alive()
            # Recreate with a distinct mtime so it registers as modified
            config_file.write_text('{"recreated": true}')
            future = time.time() + 2
            os.utime(config_file, (future, future))
            deadline = time.time() + 2.0
            while time.time() < deadline and not callback.called:
                time.sleep(0.05)
            # After the file is back and its mtime advanced, callback should fire
            assert callback.called
        finally:
            reloader.stop()


class TestTriggerReload:
    def test_callback_success(self, config_file):
        callback = Mock()
        reloader = ConfigReloader(config_file, callback)
        reloader._trigger_reload()
        assert callback.called

    def test_callback_exception_is_swallowed(self, config_file):
        """If the reload callback raises, the reloader logs and keeps running."""
        callback = Mock(side_effect=RuntimeError("config broken"))
        reloader = ConfigReloader(config_file, callback)
        # Should NOT raise - failures log at ERROR level
        reloader._trigger_reload()
        assert callback.called


class TestSignalHandler:
    def test_sighup_triggers_reload(self, config_file):
        """SIGHUP calls the reload callback via _signal_handler."""
        callback = Mock()
        reloader = ConfigReloader(config_file, callback)
        # Call the signal handler directly (simulating SIGHUP delivery)
        reloader._signal_handler(1, None)
        assert callback.called
