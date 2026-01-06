"""Tests for API endpoints"""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from ott.api.app import app
from ott.managers.radarr import RadarrManager
from ott.managers.sonarr import SonarrManager


@pytest.fixture
def mock_managers():
    """Mock Radarr and Sonarr managers"""
    mock_radarr = Mock(spec=RadarrManager)
    mock_sonarr = Mock(spec=SonarrManager)
    return mock_radarr, mock_sonarr


@pytest.fixture
def client(mock_managers):
    """FastAPI test client"""
    return TestClient(app)


class TestHealthEndpoints:
    """Tests for health check endpoints"""
    
    def test_health_check(self, client):
        """Test /health endpoint"""
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data


class TestWebhookEndpoints:
    """Tests for webhook endpoints"""
    
    @patch('ott.api.webhooks.get_radarr_mgr')
    def test_radarr_webhook_movie_added(self, mock_get_mgr, client):
        """Test /radarr webhook with MovieAdded event"""
        mock_manager = Mock(spec=RadarrManager)
        mock_get_mgr.return_value = mock_manager
        
        payload = {
            "eventType": "Download",
            "movie": {
                "id": 123,
                "title": "Test Movie",
                "year": 2021
            }
        }
        
        response = client.post("/radarr", json=payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert mock_manager.added_hook.called
    
    @patch('ott.api.webhooks.get_sonarr_mgr')
    def test_sonarr_webhook_series_added(self, mock_get_mgr, client):
        """Test /sonarr webhook with SeriesAdd event"""
        mock_manager = Mock(spec=SonarrManager)
        mock_get_mgr.return_value = mock_manager
        
        payload = {
            "eventType": "SeriesAdd",
            "series": {
                "id": 456,
                "title": "Test Series",
                "year": 2020
            }
        }
        
        response = client.post("/sonarr", json=payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert mock_manager.added_hook.called
    
    @patch('ott.api.webhooks.get_radarr_mgr')
    def test_webhook_invalid_payload(self, mock_get_mgr, client):
        """Test webhook with invalid payload"""
        mock_manager = Mock(spec=RadarrManager)
        mock_get_mgr.return_value = mock_manager
        
        # Invalid payload - missing required fields
        payload = {"invalid": "data"}
        
        response = client.post("/radarr", json=payload)
        
        # Should still return 200 but not process
        assert response.status_code == 200


class TestTelegramEndpoints:
    """Tests for Telegram callback endpoints"""
    
    @patch('ott.api.telegram.get_radarr_mgr')
    def test_telegram_callback_movie_override(self, mock_get_mgr, client):
        """Test /telegram/callback for movie override"""
        mock_manager = Mock(spec=RadarrManager)
        mock_manager.item_type.return_value = "movie"
        
        # Mock getting item
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 123,
            "title": "Test Movie",
            "monitored": False,
            "tags": [2]  # ott-skipped
        }
        mock_manager.client.get.return_value = mock_response
        mock_manager.client.put.return_value = Mock()
        mock_manager.client.post.return_value = Mock()
        
        mock_manager.skipped_tag = 2
        mock_manager.override_tag = 3
        
        mock_get_mgr.return_value = mock_manager
        
        payload = {
            "callback_query": {
                "data": "override:movie:123"
            }
        }
        
        response = client.post("/telegram/callback", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "override_applied"
        assert "Test Movie" in data["message"]
    
    @patch('ott.api.telegram.get_sonarr_mgr')
    def test_telegram_callback_series_override(self, mock_get_mgr, client):
        """Test /telegram/callback for series override"""
        mock_manager = Mock(spec=SonarrManager)
        mock_manager.item_type.return_value = "series"
        
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 456,
            "title": "Test Series",
            "monitored": False,
            "tags": [2]
        }
        mock_manager.client.get.return_value = mock_response
        mock_manager.client.put.return_value = Mock()
        mock_manager.client.post.return_value = Mock()
        
        mock_manager.skipped_tag = 2
        mock_manager.override_tag = 3
        
        mock_get_mgr.return_value = mock_manager
        
        payload = {
            "callback_query": {
                "data": "override:series:456"
            }
        }
        
        response = client.post("/telegram/callback", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "override_applied"
    
    def test_telegram_callback_invalid_format(self, client):
        """Test /telegram/callback with invalid callback data"""
        payload = {
            "callback_query": {
                "data": "invalid_format"
            }
        }
        
        response = client.post("/telegram/callback", json=payload)
        
        # Should handle gracefully
        assert response.status_code == 200
    
    def test_telegram_callback_missing_item(self, client):
        """Test /telegram/callback when item not found"""
        with patch('ott.api.telegram.get_radarr_mgr') as mock_get_mgr:
            mock_manager = Mock(spec=RadarrManager)
            mock_manager.item_type.return_value = "movie"
            mock_manager.client.get.return_value = None  # Item not found
            
            mock_get_mgr.return_value = mock_manager
            
            payload = {
                "callback_query": {
                    "data": "override:movie:999"
                }
            }
            
            response = client.post("/telegram/callback", json=payload)
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "not found" in data["message"].lower()


class TestCronEndpoint:
    """Tests for manual cron trigger endpoint"""
    
    @patch('ott.api.health.get_radarr_mgr')
    @patch('ott.api.health.get_sonarr_mgr')
    def test_cron_endpoint(self, mock_sonarr_mgr, mock_radarr_mgr, client):
        """Test /cron manual trigger endpoint"""
        mock_radarr = Mock(spec=RadarrManager)
        mock_sonarr = Mock(spec=SonarrManager)
        
        # Mock cron_cleanup to return metrics
        from ott.models import ProcessingMetrics
        mock_radarr.cron_cleanup.return_value = ProcessingMetrics(checked=5, cleaned=2)
        mock_sonarr.cron_cleanup.return_value = ProcessingMetrics(checked=3, cleaned=1)
        
        mock_radarr_mgr.return_value = mock_radarr
        mock_sonarr_mgr.return_value = mock_sonarr
        
        response = client.get("/cron")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert "radarr" in data
        assert "sonarr" in data
        
        # Verify cron was triggered
        assert mock_radarr.cron_cleanup.called
        assert mock_sonarr.cron_cleanup.called


class TestOpenAPIDocumentation:
    """Tests for OpenAPI documentation endpoints"""
    
    def test_openapi_json(self, client):
        """Test /openapi.json endpoint"""
        response = client.get("/openapi.json")
        
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "info" in data
        assert "paths" in data
    
    def test_docs_ui(self, client):
        """Test /docs endpoint (Swagger UI)"""
        response = client.get("/docs")
        
        assert response.status_code == 200
        assert "swagger" in response.text.lower() or "openapi" in response.text.lower()
