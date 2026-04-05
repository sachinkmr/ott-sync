"""Tests for ArrClient HTTP retry/backoff behavior (§3.1)."""

from unittest.mock import Mock, patch

import pytest
import requests

from ott.clients.arr_client import ArrClient


@pytest.fixture
def client(monkeypatch):
    """ArrClient with retry delays zeroed so tests don't sleep."""
    c = ArrClient(url="http://arr:7878", api_key="k")
    monkeypatch.setattr("ott.clients.arr_client._RETRY_DELAYS", (0.0, 0.0, 0.0))
    return c


class TestRetrySuccess:
    def test_200_returns_immediately(self, client):
        """2xx response is returned on first attempt, no retry."""
        ok = Mock(ok=True, status_code=200)
        with patch.object(client._session, "request", return_value=ok) as mock_req:
            res = client.get("movie")
            assert res is ok
            assert mock_req.call_count == 1


class TestRetryOnTransientStatus:
    def test_retries_on_503_then_succeeds(self, client):
        """503 on attempt 1, 200 on attempt 2 → returns the 200."""
        fail = Mock(ok=False, status_code=503, text="down")
        ok = Mock(ok=True, status_code=200)
        with patch.object(client._session, "request", side_effect=[fail, ok]) as mock_req:
            res = client.get("movie")
            assert res is ok
            assert mock_req.call_count == 2

    def test_retries_on_429(self, client):
        fail = Mock(ok=False, status_code=429, text="slow down")
        ok = Mock(ok=True, status_code=200)
        with patch.object(client._session, "request", side_effect=[fail, ok]) as mock_req:
            res = client.get("movie")
            assert res is ok
            assert mock_req.call_count == 2

    def test_gives_up_after_three_retryable_failures(self, client):
        """3 retryable failures → None."""
        fail = Mock(ok=False, status_code=502, text="bad gateway")
        with patch.object(client._session, "request", return_value=fail) as mock_req:
            res = client.get("movie")
            assert res is None
            assert mock_req.call_count == 3


class TestNoRetryOnTerminalStatus:
    def test_404_returns_none_immediately(self, client):
        """4xx (other than 429) is not retryable."""
        fail = Mock(ok=False, status_code=404, text="not found")
        with patch.object(client._session, "request", return_value=fail) as mock_req:
            res = client.get("movie/9999")
            assert res is None
            assert mock_req.call_count == 1

    def test_401_returns_none_immediately(self, client):
        """401 auth errors aren't retried."""
        fail = Mock(ok=False, status_code=401, text="bad key")
        with patch.object(client._session, "request", return_value=fail) as mock_req:
            res = client.get("movie")
            assert res is None
            assert mock_req.call_count == 1


class TestRetryOnRequestException:
    def test_retries_on_connection_error(self, client):
        ok = Mock(ok=True, status_code=200)
        side_effect = [requests.ConnectionError("nope"), ok]
        with patch.object(client._session, "request", side_effect=side_effect) as mock_req:
            res = client.get("movie")
            assert res is ok
            assert mock_req.call_count == 2

    def test_retries_on_timeout(self, client):
        ok = Mock(ok=True, status_code=200)
        side_effect = [requests.Timeout("slow"), ok]
        with patch.object(client._session, "request", side_effect=side_effect) as mock_req:
            res = client.get("movie")
            assert res is ok
            assert mock_req.call_count == 2

    def test_gives_up_after_three_connection_errors(self, client):
        side_effect = [requests.ConnectionError("nope")] * 3
        with patch.object(client._session, "request", side_effect=side_effect) as mock_req:
            res = client.get("movie")
            assert res is None
            assert mock_req.call_count == 3


class TestDeleteQueueItem:
    def test_explicit_blocklist_false_in_params(self, client):
        """delete_queue_item sends blocklist=false literally in the URL."""
        ok = Mock(ok=True, status_code=200)
        with patch.object(client._session, "request", return_value=ok) as mock_req:
            assert client.delete_queue_item(42) is True
            _, kwargs = mock_req.call_args
            params = kwargs["params"]
            assert params["blocklist"] == "false"
            assert params["removeFromClient"] == "true"

    def test_opt_in_blocklist(self, client):
        """Caller can opt into blocklisting explicitly."""
        ok = Mock(ok=True, status_code=200)
        with patch.object(client._session, "request", return_value=ok) as mock_req:
            client.delete_queue_item(42, blocklist=True)
            params = mock_req.call_args.kwargs["params"]
            assert params["blocklist"] == "true"
