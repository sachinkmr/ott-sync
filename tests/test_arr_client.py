"""Tests for ArrClient (Radarr/Sonarr HTTP client)"""

from unittest.mock import Mock, patch

import pytest
import requests

from src.clients.arr_client import ArrClient


def test_arr_client_initialization():
    """Test ArrClient initialization"""
    client = ArrClient("http://radarr:7878", "test_api_key", timeout=60)
    
    assert client.url == "http://radarr:7878"
    assert client.api_key == "test_api_key"
    assert client.timeout == 60
    assert client._session.headers["X-Api-Key"] == "test_api_key"


def test_arr_client_strips_trailing_slash():
    """Test URL trailing slash is removed"""
    client = ArrClient("http://radarr:7878/", "test_api_key")
    
    assert client.url == "http://radarr:7878"


@patch('src.clients.arr_client.requests.Session.request')
def test_get_success(mock_request):
    """Test successful GET request"""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": 1, "title": "Test Movie"}
    mock_request.return_value = mock_response
    
    client = ArrClient("http://radarr:7878", "test_api_key")
    response = client.get("movie/1")
    
    assert response is not None
    assert response.status_code == 200
    assert mock_request.called
    
    # Verify request details
    call_args = mock_request.call_args
    assert "http://radarr:7878/api/v3/movie/1" in call_args[0]


@patch('src.clients.arr_client.requests.Session.request')
def test_get_with_params(mock_request):
    """Test GET request with query parameters"""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_request.return_value = mock_response
    
    client = ArrClient("http://radarr:7878", "test_api_key")
    response = client.get("movie", params={"monitored": True})
    
    assert response is not None
    assert mock_request.called


@patch('src.clients.arr_client.requests.Session.request')
def test_post_success(mock_request):
    """Test successful POST request"""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_request.return_value = mock_response
    
    client = ArrClient("http://radarr:7878", "test_api_key")
    payload = {"name": "Test Command"}
    response = client.post("command", json=payload)
    
    assert response is not None
    assert response.status_code == 201
    
    # Verify JSON payload was sent
    call_args = mock_request.call_args
    assert call_args[1]["json"] == payload


@patch('src.clients.arr_client.requests.Session.request')
def test_put_success(mock_request):
    """Test successful PUT request"""
    mock_response = Mock()
    mock_response.status_code = 202
    mock_request.return_value = mock_response
    
    client = ArrClient("http://radarr:7878", "test_api_key")
    payload = {"id": 1, "monitored": False}
    response = client.put("movie/1", json=payload)
    
    assert response is not None
    assert response.status_code == 202


@patch('src.clients.arr_client.requests.Session.request')
def test_delete_success(mock_request):
    """Test successful DELETE request"""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_request.return_value = mock_response
    
    client = ArrClient("http://radarr:7878", "test_api_key")
    response = client.delete("moviefile/123")
    
    assert response is not None
    assert response.status_code == 200


@patch('src.clients.arr_client.requests.Session.request')
def test_request_failure_returns_none(mock_request):
    """Test failed request returns None"""
    mock_request.side_effect = requests.RequestException("Connection error")
    
    client = ArrClient("http://radarr:7878", "test_api_key")
    response = client.get("movie")
    
    assert response is None


@patch('src.clients.arr_client.requests.Session.request')
def test_http_error_returns_none(mock_request):
    """Test HTTP error (4xx/5xx) logs error and returns None"""
    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    mock_response.ok = False
    mock_request.return_value = mock_response
    
    client = ArrClient("http://radarr:7878", "test_api_key")
    response = client.get("movie/999")
    
    # arr_client returns None on error (not response.ok)
    assert response is None


@patch('src.clients.arr_client.requests.Session.request')
def test_timeout_returns_none(mock_request):
    """Test timeout returns None"""
    mock_request.side_effect = requests.Timeout("Request timed out")
    
    client = ArrClient("http://radarr:7878", "test_api_key")
    response = client.get("movie", timeout=1)
    
    assert response is None


@patch('src.clients.arr_client.requests.Session.request')
def test_api_key_header_sent(mock_request):
    """Test X-Api-Key header is included"""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_request.return_value = mock_response
    
    client = ArrClient("http://radarr:7878", "my_secret_key")
    client.get("system/status")
    
    # Verify session has the API key header
    assert client._session.headers["X-Api-Key"] == "my_secret_key"
