"""Tests for configuration management"""

import json
from pathlib import Path

import pytest

from ott.config import Config
from ott.exceptions import ConfigurationError


def test_config_initialization(sample_config_dict):
    """Test Config initialization from dictionary"""
    config = Config(sample_config_dict)
    
    assert config.radarr_url == "http://radarr:7878"
    assert config.radarr_api_key == "test_radarr_api_key_12345"
    assert config.sonarr_url == "http://sonarr:8989"
    assert config.sonarr_api_key == "test_sonarr_api_key_67890"
    assert config.ott_providers == ["Netflix", "Prime Video", "Disney Plus Hotstar"]
    assert config.region == "IN"
    assert config.cron_initial_delay_seconds == 60
    assert config.cron_interval_hours == 24


def test_config_telegram_settings(sample_config_dict):
    """Test Telegram configuration"""
    config = Config(sample_config_dict)
    
    assert config.telegram["enabled"] is True
    assert config.telegram["bot_token"] == "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    assert config.telegram["chat_id"] == "-1001234567890"


def test_config_get_method(sample_config_dict):
    """Test Config.get() backwards compatibility"""
    config = Config(sample_config_dict)
    
    assert config.get("radarr_url") == "http://radarr:7878"
    assert config.get("nonexistent_key", "default") == "default"
    assert config.get("region") == "IN"


def test_config_load_from_file(temp_config_file):
    """Test loading config from JSON file"""
    config = Config.load(temp_config_file)
    
    assert config.radarr_url == "http://radarr:7878"
    assert config.radarr_api_key == "test_radarr_api_key_12345"
    assert isinstance(config.ott_providers, list)


def test_config_load_missing_file():
    """Test loading from non-existent file raises error"""
    with pytest.raises(FileNotFoundError):
        Config.load(Path("/nonexistent/config.json"))


def test_config_load_invalid_json(tmp_path):
    """Test loading invalid JSON raises error"""
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("{ invalid json }")
    
    with pytest.raises(json.JSONDecodeError):
        Config.load(invalid_file)


def test_config_missing_required_keys(tmp_path):
    """Test missing required keys raises error"""
    incomplete_config = {
        "radarr_url": "http://radarr:7878",
        # Missing radarr_api_key
        "sonarr_url": "http://sonarr:8989",
    }
    
    config_file = tmp_path / "incomplete.json"
    config_file.write_text(json.dumps(incomplete_config))
    
    with pytest.raises(ConfigurationError, match="Missing required config keys"):
        Config.load(config_file)


def test_config_default_values(tmp_path):
    """Test default values for optional keys"""
    minimal_config = {
        "radarr_url": "http://radarr:7878",
        "radarr_api_key": "key1",
        "sonarr_url": "http://sonarr:8989",
        "sonarr_api_key": "key2",
        "ott_providers": ["Netflix"]
    }
    
    config_file = tmp_path / "minimal.json"
    config_file.write_text(json.dumps(minimal_config))
    
    config = Config.load(config_file)
    
    # Test defaults
    assert config.telegram == {}
    assert config.region == "IN"
    assert config.cron_initial_delay_seconds == 60
    assert config.cron_interval_hours == 24
