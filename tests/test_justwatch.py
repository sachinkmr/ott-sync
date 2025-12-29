"""Tests for JustWatch OTT provider lookup client"""

from unittest.mock import Mock, patch

import pytest

from src.clients.justwatch import JustWatchClient


def test_justwatch_initialization():
    """Test JustWatch client initialization"""
    client = JustWatchClient(region="IN", language="en", max_results=5)
    
    assert client.region == "IN"
    assert client.language == "en"
    assert client.max_results == 5


def test_justwatch_default_values():
    """Test JustWatch client default values"""
    client = JustWatchClient()
    
    assert client.region == "IN"
    assert client.language == "en"
    assert client.max_results == 5


@patch('src.clients.justwatch.search')
def test_get_providers_found_on_ott(mock_search):
    """Test finding content on allowed OTT provider"""
    # Mock JustWatch search results
    mock_search.return_value = [
        {
            "title": "Stranger Things",
            "original_release_year": 2016,
            "offers": [
                {"monetization_type": "flatrate", "provider_id": 8},  # Netflix
            ]
        }
    ]
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Stranger Things", 2016, {"Netflix"})
    
    assert providers == ["Netflix"]
    assert mock_search.called


@patch('src.clients.justwatch.search')
def test_get_providers_not_found(mock_search):
    """Test content not on any allowed providers"""
    mock_search.return_value = [
        {
            "title": "Obscure Movie",
            "original_release_year": 2020,
            "offers": [
                {"monetization_type": "rent", "provider_id": 2},  # iTunes (not in allowed)
            ]
        }
    ]
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Obscure Movie", 2020, {"Netflix", "Prime Video"})
    
    assert providers == []


@patch('src.clients.justwatch.search')
def test_get_providers_multiple_providers(mock_search):
    """Test content available on multiple OTT providers"""
    mock_search.return_value = [
        {
            "title": "Popular Show",
            "original_release_year": 2021,
            "offers": [
                {"monetization_type": "flatrate", "provider_id": 8},   # Netflix
                {"monetization_type": "flatrate", "provider_id": 119}, # Prime Video
            ]
        }
    ]
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers(
        "Popular Show", 
        2021, 
        {"Netflix", "Prime Video", "Disney Plus Hotstar"}
    )
    
    # Should return all matching providers
    assert "Netflix" in providers
    assert "Prime Video" in providers
    assert len(providers) == 2


@patch('src.clients.justwatch.search')
def test_get_providers_wrong_year_filtered(mock_search):
    """Test year filtering works correctly"""
    mock_search.return_value = [
        {
            "title": "Test Movie",
            "original_release_year": 2020,  # Different year
            "offers": [
                {"monetization_type": "flatrate", "provider_id": 8},
            ]
        }
    ]
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Test Movie", 2021, {"Netflix"})
    
    # Should not match due to year mismatch
    assert providers == []


@patch('src.clients.justwatch.search')
def test_get_providers_no_year(mock_search):
    """Test search without year parameter"""
    mock_search.return_value = [
        {
            "title": "Test Movie",
            "offers": [
                {"monetization_type": "flatrate", "provider_id": 8},
            ]
        }
    ]
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Test Movie", None, {"Netflix"})
    
    # Should match without year check
    assert "Netflix" in providers


@patch('src.clients.justwatch.search')
def test_get_providers_api_exception(mock_search):
    """Test API failure returns None (fail-open)"""
    mock_search.side_effect = Exception("API Error")
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Test Movie", 2021, {"Netflix"})
    
    # Should return None to indicate infrastructure failure
    assert providers is None


@patch('src.clients.justwatch.search')
def test_get_providers_no_results(mock_search):
    """Test no search results"""
    mock_search.return_value = []
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Unknown Movie", 2021, {"Netflix"})
    
    assert providers == []


@patch('src.clients.justwatch.search')
def test_get_providers_no_offers(mock_search):
    """Test content found but no streaming offers"""
    mock_search.return_value = [
        {
            "title": "Test Movie",
            "original_release_year": 2021,
            "offers": []  # No offers
        }
    ]
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Test Movie", 2021, {"Netflix"})
    
    assert providers == []


@patch('src.clients.justwatch.search')
def test_get_providers_only_rent_buy(mock_search):
    """Test content only available for rent/buy (not flatrate)"""
    mock_search.return_value = [
        {
            "title": "Test Movie",
            "original_release_year": 2021,
            "offers": [
                {"monetization_type": "rent", "provider_id": 8},
                {"monetization_type": "buy", "provider_id": 8},
            ]
        }
    ]
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Test Movie", 2021, {"Netflix"})
    
    # Should not match - only flatrate (subscription) counts
    assert providers == []
