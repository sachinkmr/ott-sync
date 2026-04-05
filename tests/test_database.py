"""Tests for DatabaseClient and all repository CRUD operations.

Uses a per-test isolated SQLite DB and monkey-patches the module-level
client reference so repositories (which call get_db()) see our instance.
"""

import pytest

from ott.db import client as db_client
from ott.db.client import DatabaseClient
from ott.db.repositories.errors import ErrorRepository
from ott.db.repositories.metrics import MetricsRepository
from ott.db.repositories.override import OverrideRepository
from ott.db.repositories.tags import TagRepository
from ott.exceptions import DatabaseError
from ott.models import ProcessingMetrics


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated DB per test."""
    d = DatabaseClient(db_path=str(tmp_path / "test.db"), enable_wal=False)
    d.initialize()
    monkeypatch.setattr(db_client, "db", d)
    yield d
    d.close(timeout=5.0)


# ---------------------------------------------------------------------------
# DatabaseClient
# ---------------------------------------------------------------------------

class TestDatabaseClientLifecycle:
    def test_initialize_creates_tables(self, tmp_path):
        """initialize() creates the schema on first run."""
        d = DatabaseClient(db_path=str(tmp_path / "fresh.db"), enable_wal=False)
        d.initialize()
        # If initialize() succeeded we can health_check()
        health = d.health_check()
        assert health["status"] == "healthy"
        d.close()

    def test_health_check_returns_status(self, db):
        health = db.health_check()
        assert health["status"] == "healthy"
        assert "database" in health

    def test_session_yields_usable_session(self, db):
        from sqlalchemy import text
        with db.session() as session:
            row = session.execute(text("SELECT 1")).scalar()
            assert row == 1

    def test_session_rolls_back_on_exception(self, db):
        """Exceptions inside the with-block trigger rollback."""
        from sqlalchemy import text
        with pytest.raises(RuntimeError):
            with db.session() as session:
                session.execute(text("SELECT 1"))
                raise RuntimeError("user code boom")

    def test_close_refuses_new_sessions_while_closing(self, db):
        """During close() (i.e. when _closing is True), session() raises.

        After close() completes, _closing is cleared and the engine is
        disposed - callers who try to reuse the client will re-initialize.
        """
        import threading
        import time

        # Hold a session open so close() has to wait and _closing stays True
        session_acquired = threading.Event()
        release_session = threading.Event()

        def hold_session():
            with db.session():
                session_acquired.set()
                release_session.wait(timeout=5.0)

        holder = threading.Thread(target=hold_session)
        holder.start()
        session_acquired.wait(timeout=2.0)

        # Fire close() in another thread; it'll block on the active session
        close_done = threading.Event()
        def do_close():
            db.close(timeout=3.0)
            close_done.set()
        closer = threading.Thread(target=do_close)
        closer.start()

        # Give close() a moment to flip _closing=True
        time.sleep(0.1)
        assert db._closing is True

        # A new session request while closing raises
        with pytest.raises(DatabaseError, match="shutting down"):
            with db.session():
                pass

        # Release the held session so close() can finish
        release_session.set()
        holder.join(timeout=2.0)
        closer.join(timeout=5.0)
        assert close_done.is_set()


class TestGetDbNotInitialized:
    def test_get_db_raises_when_none(self, monkeypatch):
        """get_db() raises DatabaseError when the global isn't set."""
        from ott.db.client import get_db
        monkeypatch.setattr(db_client, "db", None)
        with pytest.raises(DatabaseError, match="not initialized"):
            get_db()


# ---------------------------------------------------------------------------
# MetricsRepository
# ---------------------------------------------------------------------------

class TestMetricsRepository:
    def test_save_and_aggregate(self, db):
        metrics = ProcessingMetrics(checked=5, cleaned=2, errors=1)
        assert MetricsRepository.save_metrics(
            service="radarr", operation_type="cron", metrics=metrics,
        ) is True

        stats = MetricsRepository.get_aggregated_stats(service="radarr", hours=24)
        assert stats["total_checked"] == 5
        assert stats["total_cleaned"] == 2
        assert stats["total_errors"] == 1
        assert stats["total_operations"] == 1

    def test_filter_by_service(self, db):
        MetricsRepository.save_metrics(
            "radarr", "cron", ProcessingMetrics(checked=3),
        )
        MetricsRepository.save_metrics(
            "sonarr", "cron", ProcessingMetrics(checked=7),
        )
        radarr = MetricsRepository.get_aggregated_stats(service="radarr", hours=24)
        sonarr = MetricsRepository.get_aggregated_stats(service="sonarr", hours=24)
        assert radarr["total_checked"] == 3
        assert sonarr["total_checked"] == 7

    def test_empty_window_returns_zeros(self, db):
        stats = MetricsRepository.get_aggregated_stats(service="radarr", hours=1)
        assert stats["total_checked"] == 0
        assert stats["total_operations"] == 0


# ---------------------------------------------------------------------------
# OverrideRepository
# ---------------------------------------------------------------------------

class TestOverrideRepository:
    def test_log_and_fetch_history(self, db):
        assert OverrideRepository.log_override(
            item_id=42, service="radarr", item_type="movie",
            title="The Test", year=2024, user_action="override_download",
            user_name="alice", blocked_providers=["Netflix"],
        ) is True

        history = OverrideRepository.get_item_history(42, "radarr")
        assert len(history) == 1
        assert history[0]["title"] == "The Test"
        assert history[0]["user_name"] == "alice"

    def test_get_item_history_unknown_item(self, db):
        assert OverrideRepository.get_item_history(999, "radarr") == []

    def test_get_user_overrides(self, db):
        OverrideRepository.log_override(
            item_id=1, service="radarr", item_type="movie", title="A",
            year=2020, user_action="override_download",
            user_id="u1", user_name="bob",
        )
        OverrideRepository.log_override(
            item_id=2, service="radarr", item_type="movie", title="B",
            year=2021, user_action="override_download",
            user_id="u1", user_name="bob",
        )
        recent = OverrideRepository.get_user_overrides("u1", limit=10)
        assert len(recent) == 2


# ---------------------------------------------------------------------------
# TagRepository
# ---------------------------------------------------------------------------

class TestTagRepository:
    def test_log_tag_change_and_fetch(self, db):
        assert TagRepository.log_tag_change(
            item_id=1, service="radarr", item_type="movie",
            title="Test Movie", tag_name="ott-netflix", action="added",
            triggered_by="webhook",
        ) is True
        history = TagRepository.get_item_tag_history(1, "radarr")
        assert len(history) == 1
        assert history[0]["tag_name"] == "ott-netflix"
        assert history[0]["action"] == "added"

    def test_empty_history(self, db):
        assert TagRepository.get_item_tag_history(999, "radarr") == []


# ---------------------------------------------------------------------------
# ErrorRepository
# ---------------------------------------------------------------------------

class TestErrorRepository:
    def test_log_error_then_fetch(self, db):
        ok = ErrorRepository.log_error(
            error_type="JustWatchAPIError",
            error_message="timeout talking to JustWatch",
            severity="warning",
            service="radarr",
            item_id=5,
        )
        assert ok is True
        errors = ErrorRepository.get_recent_errors(limit=10)
        assert len(errors) == 1
        assert errors[0]["error_type"] == "JustWatchAPIError"
        assert errors[0]["resolved"] is False

    def test_mark_resolved(self, db):
        ErrorRepository.log_error(
            error_type="X", error_message="y", severity="error",
        )
        errors = ErrorRepository.get_recent_errors(limit=1)
        err_id = errors[0]["id"]
        assert ErrorRepository.mark_resolved(err_id) is True
        # Now resolved=True
        after = ErrorRepository.get_recent_errors(limit=1)
        assert after[0]["resolved"] is True

    def test_unresolved_only_filter(self, db):
        ErrorRepository.log_error(
            error_type="A", error_message="resolved", severity="error",
        )
        resolved_id = ErrorRepository.get_recent_errors(limit=1)[0]["id"]
        ErrorRepository.mark_resolved(resolved_id)
        ErrorRepository.log_error(
            error_type="B", error_message="active", severity="error",
        )

        all_errors = ErrorRepository.get_recent_errors(limit=10)
        unresolved = ErrorRepository.get_recent_errors(limit=10, unresolved_only=True)
        assert len(all_errors) == 2
        assert len(unresolved) == 1
        assert unresolved[0]["error_message"] == "active"

    def test_mark_resolved_unknown_id(self, db):
        assert ErrorRepository.mark_resolved(999999) is False
