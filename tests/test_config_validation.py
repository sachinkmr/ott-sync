"""Tests for Config.validate() (§3.3)."""

import copy

import pytest

from ott.config import Config
from ott.exceptions import ConfigurationError


MINIMAL = {
    "radarr_url": "http://radarr:7878",
    "radarr_api_key": "abc",
    "sonarr_url": "http://sonarr:8989",
    "sonarr_api_key": "def",
    "ott_providers": ["Netflix"],
}


def _with(**overrides):
    merged = copy.deepcopy(MINIMAL)
    merged.update(overrides)
    return merged


class TestValidateAcceptsGood:
    def test_minimal_config(self):
        Config(MINIMAL).validate()

    def test_https_urls(self):
        Config(_with(radarr_url="https://radarr.example.com")).validate()


class TestValidateRejectsBadUrls:
    def test_radarr_url_missing_scheme(self):
        with pytest.raises(ConfigurationError, match="radarr_url"):
            Config(_with(radarr_url="radarr:7878")).validate()

    def test_sonarr_url_wrong_scheme(self):
        with pytest.raises(ConfigurationError, match="sonarr_url"):
            Config(_with(sonarr_url="ftp://sonarr:8989")).validate()

    def test_empty_radarr_url(self):
        with pytest.raises(ConfigurationError, match="radarr_url"):
            Config(_with(radarr_url="")).validate()


class TestValidateRejectsEmptyApiKeys:
    def test_empty_radarr_key(self):
        with pytest.raises(ConfigurationError, match="radarr_api_key"):
            Config(_with(radarr_api_key="")).validate()

    def test_empty_sonarr_key(self):
        with pytest.raises(ConfigurationError, match="sonarr_api_key"):
            Config(_with(sonarr_api_key="")).validate()


class TestValidateRejectsEmptyProviders:
    def test_empty_provider_list(self):
        with pytest.raises(ConfigurationError, match="ott_providers"):
            Config(_with(ott_providers=[])).validate()


class TestValidateRejectsBadNumericFields:
    def test_zero_cron_interval(self):
        with pytest.raises(ConfigurationError, match="cron_interval_hours"):
            Config(_with(cron_interval_hours=0)).validate()

    def test_negative_cron_interval(self):
        with pytest.raises(ConfigurationError, match="cron_interval_hours"):
            Config(_with(cron_interval_hours=-1)).validate()

    def test_zero_rate_limit_calls(self):
        with pytest.raises(ConfigurationError, match="justwatch_rate_limit_calls"):
            Config(_with(justwatch_rate_limit={"max_calls": 0, "period_seconds": 60})).validate()

    def test_negative_verification_delay(self):
        with pytest.raises(ConfigurationError, match="verification_delay_seconds"):
            Config(_with(verification_delay_seconds=-5)).validate()

    def test_zero_verification_delay_is_ok(self):
        """Zero delay means 'don't wait' - valid."""
        Config(_with(verification_delay_seconds=0)).validate()


class TestValidateGathersMultipleErrors:
    def test_reports_all_bad_fields_in_one_error(self):
        bad = _with(
            radarr_url="not-a-url",
            sonarr_api_key="",
            ott_providers=[],
            cron_interval_hours=0,
        )
        with pytest.raises(ConfigurationError) as exc_info:
            Config(bad).validate()
        msg = str(exc_info.value)
        assert "radarr_url" in msg
        assert "sonarr_api_key" in msg
        assert "ott_providers" in msg
        assert "cron_interval_hours" in msg


class TestBackupIntervalConditional:
    def test_backup_disabled_no_check(self):
        """When backup_enabled=false the interval isn't validated."""
        bad = _with(database={"backup_enabled": False, "backup_interval_hours": 0})
        Config(bad).validate()  # should not raise

    def test_backup_enabled_zero_interval_rejected(self):
        bad = _with(database={"backup_enabled": True, "backup_interval_hours": 0})
        with pytest.raises(ConfigurationError, match="database_backup_interval_hours"):
            Config(bad).validate()
