"""Tests for JustWatch OTT provider lookup client"""

from types import SimpleNamespace
from unittest.mock import patch

from ott.clients.justwatch import JustWatchClient


def _offer(provider_name: str) -> SimpleNamespace:
    """Build a simple-justwatch-style offer object with offer.package.name."""
    return SimpleNamespace(package=SimpleNamespace(name=provider_name))


def _item(
    title: str = "Sample",
    release_year: int | None = 2020,
    offers: list | None = None,
    tmdb_id: int | None = None,
    imdb_id: str | None = None,
) -> SimpleNamespace:
    """Build a simple-justwatch-style result object."""
    ns = SimpleNamespace(title=title, offers=offers or [])
    if release_year is not None:
        ns.release_year = release_year
    if tmdb_id is not None:
        ns.tmdb_id = tmdb_id
    if imdb_id is not None:
        ns.imdb_id = imdb_id
    return ns


def test_justwatch_initialization():
    """JustWatch client initialization"""
    client = JustWatchClient(region="IN", language="en", max_results=5)
    assert client.region == "IN"
    assert client.language == "en"
    assert client.max_results == 5


def test_justwatch_default_values():
    """JustWatch client default values"""
    client = JustWatchClient()
    assert client.region == "IN"
    assert client.language == "en"
    assert client.max_results == 5


@patch("ott.clients.justwatch.search")
def test_get_providers_found_on_ott(mock_search):
    """Item available on one allowed provider → returns that provider."""
    mock_search.return_value = [
        _item("Stranger Things", 2016, offers=[_offer("Netflix")]),
    ]
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Stranger Things", 2016, {"Netflix"})
    assert providers == ["Netflix"]
    assert mock_search.called


@patch("ott.clients.justwatch.search")
def test_get_providers_not_found(mock_search):
    """Item only on unsupported providers → empty list."""
    mock_search.return_value = [
        _item("Obscure Movie", 2020, offers=[_offer("Apple TV+")]),
    ]
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Obscure Movie", 2020, {"Netflix", "Prime Video"})
    assert providers == []


@patch("ott.clients.justwatch.search")
def test_get_providers_multiple_providers(mock_search):
    """All matching providers are returned."""
    mock_search.return_value = [
        _item(
            "Popular Show", 2021,
            offers=[_offer("Netflix"), _offer("Prime Video")],
        ),
    ]
    client = JustWatchClient(region="IN")
    providers = client.get_providers(
        "Popular Show", 2021, {"Netflix", "Prime Video", "Disney Plus Hotstar"},
    )
    assert set(providers) == {"Netflix", "Prime Video"}


@patch("ott.clients.justwatch.search")
def test_get_providers_wrong_year_filtered(mock_search):
    """Year mismatch >1 year filters the result out."""
    mock_search.return_value = [
        _item("Test Movie", 2015, offers=[_offer("Netflix")]),
    ]
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Test Movie", 2021, {"Netflix"})
    assert providers == []


@patch("ott.clients.justwatch.search")
def test_get_providers_no_year(mock_search):
    """None year bypasses year filter."""
    mock_search.return_value = [
        _item("Test Movie", release_year=None, offers=[_offer("Netflix")]),
    ]
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Test Movie", None, {"Netflix"})
    assert "Netflix" in providers


@patch("ott.clients.justwatch.search")
def test_get_providers_tmdb_id_match_overrides_year(mock_search):
    """tmdb_id match accepts the result even when year would exclude it."""
    mock_search.return_value = [
        _item(
            "Stranger Things", release_year=2010,  # wrong year
            offers=[_offer("Netflix")], tmdb_id=66732,
        ),
    ]
    client = JustWatchClient(region="IN")
    providers = client.get_providers(
        "Stranger Things", 2016, {"Netflix"}, tmdb_id=66732,
    )
    assert providers == ["Netflix"]


@patch("ott.clients.justwatch.search")
def test_get_providers_skips_missing_package(mock_search):
    """Offers without package.name are ignored, not crashed on."""
    broken_offer = SimpleNamespace(package=None)
    mock_search.return_value = [
        _item(
            "Movie", 2020,
            offers=[broken_offer, _offer("Netflix")],
        ),
    ]
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Movie", 2020, {"Netflix"})
    assert providers == ["Netflix"]


@patch('ott.clients.justwatch.search')
def test_get_providers_api_exception(mock_search):
    """Test API failure returns None (fail-open)"""
    mock_search.side_effect = Exception("API Error")
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Test Movie", 2021, {"Netflix"})
    
    # Should return None to indicate infrastructure failure
    assert providers is None


@patch('ott.clients.justwatch.search')
def test_get_providers_no_results(mock_search):
    """Test no search results"""
    mock_search.return_value = []
    
    client = JustWatchClient(region="IN")
    providers = client.get_providers("Unknown Movie", 2021, {"Netflix"})
    
    assert providers == []


@patch('ott.clients.justwatch.search')
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


@patch('ott.clients.justwatch.search')
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
