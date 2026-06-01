"""Tests for OTT managers"""

from typing import Any
from unittest.mock import Mock

import pytest

from ott.managers.radarr import RadarrManager
from ott.managers.sonarr import SonarrManager


def _make_arr_mock(
    tag_responses: list[dict[str, Any]] | None = None,
    item_by_id: dict[int, dict[str, Any]] | None = None,
    queue: list[dict[str, Any]] | None = None,
) -> Mock:
    """Build an ArrClient mock whose .get/.post/.put/.delete answer by URL.

    Avoids the ordering-dependent side_effect lists that break whenever the
    manager code's call sequence changes.
    """
    tag_responses = tag_responses or []
    item_by_id = item_by_id or {}
    queue = queue or []

    client = Mock()
    client.url = "http://arr:7878"

    def _get(endpoint: str, **kwargs):
        res = Mock()
        if endpoint == "tag":
            res.json.return_value = tag_responses
            res.status_code = 200
            return res
        if endpoint == "queue":
            res.json.return_value = {
                "page": 1, "pageSize": 1000,
                "totalRecords": len(queue),
                "records": queue,
            }
            res.status_code = 200
            return res
        # Item lookups e.g. "movie/123" or "series/456"
        if "/" in endpoint:
            _, id_str = endpoint.split("/", 1)
            try:
                item_id = int(id_str)
            except ValueError:
                return None
            item = item_by_id.get(item_id)
            if item is None:
                return None
            res.json.return_value = item
            res.status_code = 200
            return res
        return None

    client.get = Mock(side_effect=_get)
    client.post = Mock(return_value=Mock(status_code=200))
    client.put = Mock(return_value=Mock(status_code=200))
    client.delete = Mock(return_value=Mock(status_code=200))

    # Helper methods that the queue-cleanup flow uses
    def _get_queue(page_size: int = 1000):
        return queue
    client.get_queue = Mock(side_effect=_get_queue)
    client.delete_queue_item = Mock(return_value=True)

    return client


@pytest.fixture
def arr_mock(sample_tag_response):
    """ArrClient mock with canned tag responses."""
    return _make_arr_mock(tag_responses=sample_tag_response)


class TestRadarrManager:
    """Tests for RadarrManager"""

    def test_item_type(self, arr_mock, mock_justwatch, mock_telegram):
        """RadarrManager reports 'movie' as item type."""
        manager = RadarrManager(
            arr_client=arr_mock,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"},
        )
        assert manager.item_type() == "movie"

    def test_fetch_items(self, arr_mock, mock_justwatch, mock_telegram, sample_radarr_items):
        """RadarrManager.fetch_items hits GET /movie."""
        # Replace the get-by-url mock with one that returns our sample list
        res = Mock()
        res.json.return_value = sample_radarr_items
        arr_mock.get = Mock(return_value=res)

        manager = RadarrManager(
            arr_client=arr_mock,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"},
        )
        items = manager.fetch_items()
        assert items == sample_radarr_items
        arr_mock.get.assert_called_once_with("movie")


class TestSonarrManager:
    """Tests for SonarrManager"""

    def test_item_type(self, arr_mock, mock_justwatch, mock_telegram):
        manager = SonarrManager(
            arr_client=arr_mock,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"},
        )
        assert manager.item_type() == "series"

    def test_fetch_items(self, arr_mock, mock_justwatch, mock_telegram):
        sample_series = [{"id": 1, "title": "Test Series"}]
        res = Mock()
        res.json.return_value = sample_series
        arr_mock.get = Mock(return_value=res)

        manager = SonarrManager(
            arr_client=arr_mock,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"},
        )
        items = manager.fetch_items()
        assert items == sample_series
        arr_mock.get.assert_called_once_with("series")


class TestOTTBaseManager:
    """Tests for OTTBaseManager common functionality"""

    def test_tag_cached_properties(
        self, arr_mock, mock_justwatch, mock_telegram, sample_tag_response
    ):
        """Tag IDs are resolved via GET /tag and cached."""
        manager = RadarrManager(
            arr_client=arr_mock,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"},
        )
        assert manager.processed_tag == 1
        assert manager.skipped_tag == 2
        assert manager.override_tag == 3

        # Access again - cached, no new tag requests expected beyond initial.
        _ = manager.processed_tag
        _ = manager.skipped_tag
        _ = manager.override_tag
        # Each cached_property fetches tags at most once - 3 GET("tag") calls total.
        tag_calls = [c for c in arr_mock.get.call_args_list if c.args and c.args[0] == "tag"]
        assert len(tag_calls) == 3

    def test_added_hook_ignores_unknown_event(
        self, arr_mock, mock_justwatch, mock_telegram
    ):
        """Events outside Movie/SeriesAdd/Grab/Download are skipped."""
        manager = RadarrManager(
            arr_client=arr_mock,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"},
        )
        manager.added_hook({
            "eventType": "HealthIssue",
            "movie": {"id": 999, "title": "Noise", "tags": []},
        })
        # Should not touch JustWatch for ignored events
        assert not mock_justwatch.get_providers.called

    def test_added_hook_short_circuits_on_override_tag(
        self, mock_justwatch, mock_telegram, sample_tag_response
    ):
        """ott-override tag skips the whole enforcement pipeline."""
        # item arrives already carrying override_tag (id=3 in sample_tag_response)
        arr = _make_arr_mock(tag_responses=sample_tag_response)
        manager = RadarrManager(
            arr_client=arr,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"},
        )
        manager.added_hook({
            "eventType": "MovieAdded",
            "movie": {"id": 42, "title": "Manual Add", "tags": [3]},
        })
        # No JustWatch call, no item update
        assert not mock_justwatch.get_providers.called
        assert not arr.put.called


def test_unmonitor_defaults(arr_mock, mock_justwatch, mock_telegram):
    """Managers default to enabled + 720p before main.py overrides them."""
    m = RadarrManager(
        arr_client=arr_mock, justwatch_client=mock_justwatch,
        telegram=mock_telegram, ott_providers={"Netflix"},
    )
    assert m.unmonitor_on_download_enabled is True
    assert m.unmonitor_on_download_min_resolution == 720


def test_file_resolution(arr_mock, mock_justwatch, mock_telegram):
    """_file_resolution reads quality.quality.resolution, 0 when missing."""
    m = RadarrManager(
        arr_client=arr_mock, justwatch_client=mock_justwatch,
        telegram=mock_telegram, ott_providers={"Netflix"},
    )
    assert m._file_resolution({"quality": {"quality": {"resolution": 1080}}}) == 1080
    assert m._file_resolution({"quality": {"quality": {"resolution": 0}}}) == 0
    assert m._file_resolution({"quality": {"quality": {}}}) == 0
    assert m._file_resolution({}) == 0
    assert m._file_resolution(None) == 0
