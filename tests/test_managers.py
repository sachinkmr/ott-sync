"""Tests for OTT managers"""

from unittest.mock import Mock, call, patch

import pytest

from src.managers.radarr import RadarrManager
from src.managers.sonarr import SonarrManager
from src.models import ProcessingMetrics


class TestRadarrManager:
    """Tests for RadarrManager"""
    
    def test_item_type(self, mock_arr_client, mock_justwatch, mock_telegram):
        """Test RadarrManager returns 'movie' as item type"""
        manager = RadarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        assert manager.item_type() == "movie"
    
    def test_fetch_items(self, mock_arr_client, mock_justwatch, mock_telegram, sample_radarr_items):
        """Test RadarrManager fetches movies from API"""
        mock_response = Mock()
        mock_response.json.return_value = sample_radarr_items
        mock_arr_client.get.return_value = mock_response
        
        manager = RadarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        items = manager.fetch_items()
        
        assert items == sample_radarr_items
        mock_arr_client.get.assert_called_once_with("movie")


class TestSonarrManager:
    """Tests for SonarrManager"""
    
    def test_item_type(self, mock_arr_client, mock_justwatch, mock_telegram):
        """Test SonarrManager returns 'series' as item type"""
        manager = SonarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        assert manager.item_type() == "series"
    
    def test_fetch_items(self, mock_arr_client, mock_justwatch, mock_telegram):
        """Test SonarrManager fetches series from API"""
        sample_series = [{"id": 1, "title": "Test Series"}]
        mock_response = Mock()
        mock_response.json.return_value = sample_series
        mock_arr_client.get.return_value = mock_response
        
        manager = SonarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        items = manager.fetch_items()
        
        assert items == sample_series
        mock_arr_client.get.assert_called_once_with("series")


class TestOTTBaseManager:
    """Tests for OTTBaseManager common functionality"""
    
    def test_tag_creation(self, mock_arr_client, mock_justwatch, mock_telegram, sample_tag_response):
        """Test tag IDs are fetched and cached"""
        mock_response = Mock()
        mock_response.json.return_value = sample_tag_response
        mock_arr_client.get.return_value = mock_response
        
        manager = RadarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        # Access cached properties
        assert manager.processed_tag == 1
        assert manager.skipped_tag == 2
        assert manager.override_tag == 3
        
        # Should only fetch tags once (cached)
        assert mock_arr_client.get.call_count == 3  # One call per tag
    
    def test_enforce_block_basic(self, mock_arr_client, mock_justwatch, mock_telegram, sample_tag_response):
        """Test enforce_block cancels downloads and unmonitors"""
        # Setup mocks
        mock_tag_response = Mock()
        mock_tag_response.json.return_value = sample_tag_response
        
        mock_item_response = Mock()
        mock_item_response.json.return_value = {
            "id": 123,
            "title": "Test Movie",
            "monitored": True,
            "tags": []
        }
        
        mock_arr_client.get.side_effect = [
            mock_tag_response,  # For skipped_tag
            mock_tag_response,  # For processed_tag
            mock_item_response  # For getting item
        ]
        mock_arr_client.post.return_value = Mock()
        mock_arr_client.delete.return_value = Mock()
        mock_arr_client.put.return_value = Mock()
        
        manager = RadarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        manager.enforce_block(123, delete_files=False)
        
        # Verify CancelPendingDownloads command was sent
        assert mock_arr_client.post.called
        post_call = mock_arr_client.post.call_args
        assert post_call[0][0] == "command"
        assert "CancelPendingDownloads" in post_call[1]["json"]["name"]
        
        # Verify item was updated
        assert mock_arr_client.put.called
        put_call = mock_arr_client.put.call_args
        assert put_call[0][0] == "movie/123"
    
    def test_enforce_block_with_file_deletion(self, mock_arr_client, mock_justwatch, mock_telegram, sample_tag_response):
        """Test enforce_block deletes files when requested"""
        # Setup mocks
        mock_tag_response = Mock()
        mock_tag_response.json.return_value = sample_tag_response
        
        mock_item_response = Mock()
        mock_item_response.json.return_value = {
            "id": 123,
            "title": "Test Movie",
            "monitored": True,
            "tags": [],
            "hasFile": True,
            "movieFile": {"id": 999}
        }
        
        mock_arr_client.get.side_effect = [
            mock_tag_response,  # skipped_tag
            mock_tag_response,  # processed_tag
            mock_item_response  # item
        ]
        mock_arr_client.post.return_value = Mock()
        mock_arr_client.put.return_value = Mock()
        mock_arr_client.delete.return_value = Mock()
        
        manager = RadarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        manager.enforce_block(123, delete_files=True)
        
        # Verify file deletion was attempted
        delete_calls = [call for call in mock_arr_client.delete.call_args_list 
                       if 'moviefile' in str(call)]
        assert len(delete_calls) > 0
    
    def test_added_hook_blocks_ott_content(self, mock_arr_client, mock_justwatch, mock_telegram, 
                                           sample_tag_response, sample_movie_data):
        """Test webhook blocks content found on OTT"""
        # Setup mocks
        mock_tag_response = Mock()
        mock_tag_response.json.return_value = sample_tag_response
        
        mock_item_response = Mock()
        mock_item_response.json.return_value = {
            "id": 123,
            "title": "Test Movie",
            "monitored": True,
            "tags": []
        }
        
        mock_arr_client.get.side_effect = [
            mock_tag_response,  # processed_tag
            mock_tag_response,  # skipped_tag  
            mock_tag_response,  # override_tag
            mock_item_response,  # get item for enforcement
        ]
        mock_arr_client.post.return_value = Mock()
        mock_arr_client.put.return_value = Mock()
        mock_arr_client.delete.return_value = Mock()
        
        # Mock JustWatch finding content on Netflix
        mock_justwatch.get_providers.return_value = ["Netflix"]
        
        # Mock Telegram
        mock_telegram.send_photo.return_value = True
        
        manager = RadarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        manager.added_hook(sample_movie_data)
        
        # Verify JustWatch was called
        assert mock_justwatch.get_providers.called
        
        # Verify Telegram notification was sent
        assert mock_telegram.send_photo.called or mock_telegram.send.called
        
        # Verify enforcement happened
        assert mock_arr_client.put.called
    
    def test_added_hook_allows_non_ott_content(self, mock_arr_client, mock_justwatch, 
                                                mock_telegram, sample_tag_response, sample_movie_data):
        """Test webhook allows content NOT on OTT"""
        mock_tag_response = Mock()
        mock_tag_response.json.return_value = sample_tag_response
        
        mock_item_response = Mock()
        mock_item_response.json.return_value = {
            "id": 123,
            "title": "Test Movie",
            "monitored": True,
            "tags": []
        }
        
        mock_arr_client.get.side_effect = [
            mock_tag_response,  # processed_tag
            mock_tag_response,  # skipped_tag
            mock_tag_response,  # override_tag
            mock_item_response,  # get item for tagging
        ]
        mock_arr_client.put.return_value = Mock()
        
        # Mock JustWatch NOT finding content
        mock_justwatch.get_providers.return_value = []
        
        manager = RadarrManager(
            arr_client=mock_arr_client,
            justwatch_client=mock_justwatch,
            telegram=mock_telegram,
            ott_providers={"Netflix"}
        )
        
        manager.added_hook(sample_movie_data)
        
        # Verify no Telegram notification (no OTT found)
        assert not mock_telegram.send_photo.called
        assert not mock_telegram.send.called
        
        # Item should be tagged as processed but NOT blocked
        assert mock_arr_client.put.called
        put_call = mock_arr_client.put.call_args
        item_data = put_call[1]["json"]
        assert 1 in item_data["tags"]  # ott-processed
        assert 2 not in item_data["tags"]  # NOT ott-skipped
