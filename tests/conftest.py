"""Test configuration and shared fixtures"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from ott.clients.arr_client import ArrClient
from ott.clients.justwatch import JustWatchClient
from ott.clients.telegram import TelegramNotifier
from ott.config import Config


@pytest.fixture
def sample_config_dict() -> dict[str, Any]:
    """Sample configuration dictionary"""
    return {
        "radarr_url": "http://radarr:7878",
        "radarr_api_key": "test_radarr_api_key_12345",
        "sonarr_url": "http://sonarr:8989",
        "sonarr_api_key": "test_sonarr_api_key_67890",
        "ott_providers": ["Netflix", "Prime Video", "Disney Plus Hotstar"],
        "telegram": {
            "enabled": True,
            "bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            "chat_id": "-1001234567890",
            "region": "India"
        },
        "region": "IN",
        "cron_initial_delay_seconds": 60,
        "cron_interval_hours": 24
    }


@pytest.fixture
def temp_config_file(tmp_path: Path, sample_config_dict: dict) -> Path:
    """Create temporary config file"""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(sample_config_dict))
    return config_file


@pytest.fixture
def config(sample_config_dict: dict) -> Config:
    """Config instance from sample dictionary"""
    return Config(sample_config_dict)


@pytest.fixture
def mock_telegram() -> TelegramNotifier:
    """Mock Telegram notifier"""
    telegram_config = {
        "enabled": True,
        "bot_token": "test_token",
        "chat_id": "test_chat",
        "region": "India"
    }
    notifier = TelegramNotifier(telegram_config)
    return notifier


@pytest.fixture
def mock_justwatch() -> Mock:
    """Mock JustWatch client"""
    client = Mock(spec=JustWatchClient)
    client.region = "IN"
    client.language = "en"
    return client


@pytest.fixture
def mock_arr_client() -> Mock:
    """Mock *arr client"""
    client = Mock(spec=ArrClient)
    client.url = "http://radarr:7878"
    return client


@pytest.fixture
def sample_movie_data() -> dict[str, Any]:
    """Sample movie data from Radarr webhook"""
    return {
        "movie": {
            "id": 123,
            "title": "Stranger Things",
            "year": 2016,
            "monitored": True,
            "hasFile": False,
            "tags": [],
            "images": [
                {
                    "coverType": "poster",
                    "remoteUrl": "https://image.tmdb.org/poster.jpg"
                }
            ]
        },
        "eventType": "Download"
    }


@pytest.fixture
def sample_series_data() -> dict[str, Any]:
    """Sample series data from Sonarr webhook"""
    return {
        "series": {
            "id": 456,
            "title": "The Crown",
            "year": 2016,
            "monitored": True,
            "tags": [],
            "images": [
                {
                    "coverType": "poster",
                    "remoteUrl": "https://image.tmdb.org/series_poster.jpg"
                }
            ]
        },
        "eventType": "SeriesAdd"
    }


@pytest.fixture
def sample_radarr_items() -> list[dict[str, Any]]:
    """Sample list of movies from Radarr API"""
    return [
        {
            "id": 1,
            "title": "Movie with OTT",
            "year": 2020,
            "monitored": True,
            "hasFile": True,
            "tags": [],
            "movieFile": {"id": 100}
        },
        {
            "id": 2,
            "title": "Movie without OTT",
            "year": 2021,
            "monitored": True,
            "hasFile": False,
            "tags": []
        },
        {
            "id": 3,
            "title": "Already processed",
            "year": 2019,
            "monitored": False,
            "hasFile": False,
            "tags": [1]  # ott-processed tag
        }
    ]


@pytest.fixture
def sample_tag_response() -> list[dict[str, Any]]:
    """Sample tag response from *arr API"""
    return [
        {"id": 1, "label": "ott-processed"},
        {"id": 2, "label": "ott-skipped"},
        {"id": 3, "label": "ott-override"}
    ]
