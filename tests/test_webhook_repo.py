"""Integration tests for WebhookRepository against a real SQLite DB."""

import pytest

from ott.db import client as db_client
from ott.db.client import DatabaseClient
from ott.db.repositories.webhook import WebhookRepository


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Create an isolated DB for each test and rebind the global."""
    d = DatabaseClient(db_path=str(tmp_path / "test.db"), enable_wal=False)
    d.initialize()
    # The repositories call get_db() which reads the module-level `db`
    # binding; swap it for ours so they hit this isolated instance.
    monkeypatch.setattr(db_client, "db", d)
    yield d
    d.close(timeout=5.0)


class TestLogWebhook:
    def test_log_success_returns_true(self, db):
        ok = WebhookRepository.log_webhook(
            service="radarr",
            event_type="MovieAdded",
            item_id=123,
            title="Test Movie",
            payload={"movie": {"id": 123}, "eventType": "MovieAdded"},
            status="success",
        )
        assert ok is True

    def test_log_with_error_message(self, db):
        ok = WebhookRepository.log_webhook(
            service="sonarr",
            event_type="SeriesAdded",
            item_id=456,
            title="Test Series",
            payload={"series": {"id": 456}},
            status="error",
            error_message="manager blew up",
        )
        assert ok is True


class TestCheckDuplicate:
    def test_first_webhook_not_a_duplicate(self, db):
        payload = {"movie": {"id": 1}, "eventType": "MovieAdded"}
        assert WebhookRepository.check_duplicate(payload, window_seconds=60) is False

    def test_retry_within_window_is_duplicate(self, db):
        """log then check with the same payload returns True."""
        payload = {"movie": {"id": 1}, "eventType": "MovieAdded"}
        WebhookRepository.log_webhook(
            service="radarr", event_type="MovieAdded",
            item_id=1, title="M", payload=payload, status="success",
        )
        assert WebhookRepository.check_duplicate(payload, window_seconds=60) is True

    def test_different_payload_not_a_duplicate(self, db):
        """Two distinct payloads (different ids) hash differently."""
        p1 = {"movie": {"id": 1}, "eventType": "MovieAdded"}
        p2 = {"movie": {"id": 2}, "eventType": "MovieAdded"}
        WebhookRepository.log_webhook(
            service="radarr", event_type="MovieAdded",
            item_id=1, title="M", payload=p1, status="success",
        )
        assert WebhookRepository.check_duplicate(p2, window_seconds=60) is False

    def test_error_status_still_counts_as_duplicate(self, db):
        """A failed processing also populates the dedup window."""
        payload = {"movie": {"id": 5}, "eventType": "MovieAdded"}
        WebhookRepository.log_webhook(
            service="radarr", event_type="MovieAdded",
            item_id=5, title="M", payload=payload, status="error",
            error_message="boom",
        )
        assert WebhookRepository.check_duplicate(payload, window_seconds=60) is True

    def test_dedup_window_respects_lookback(self, db):
        """Old entries outside the window don't count."""
        from datetime import datetime, timedelta
        from ott.db.schema import WebhookLogModel

        payload = {"movie": {"id": 9}, "eventType": "MovieAdded"}
        # Insert an old record directly
        with db.session() as session:
            import hashlib
            import json
            phash = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
            old = WebhookLogModel(
                timestamp=datetime.utcnow() - timedelta(seconds=120),
                service="radarr", event_type="MovieAdded", item_id=9,
                title="M", payload_hash=phash, status="success",
            )
            session.add(old)
            session.commit()

        # 60s window excludes the 120s-old entry
        assert WebhookRepository.check_duplicate(payload, window_seconds=60) is False
        # 300s window includes it
        assert WebhookRepository.check_duplicate(payload, window_seconds=300) is True
