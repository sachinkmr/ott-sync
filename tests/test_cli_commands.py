"""Tests for CLI command registration and dispatch.

register_commands() binds typer commands into a closure over four
dependencies (radarr getter, sonarr getter, FastAPI app, config). We
use typer's CliRunner to invoke each command through its registered
interface instead of calling the functions directly.
"""

from unittest.mock import Mock

import pytest
import typer
from typer.testing import CliRunner

from ott.cli.commands import app, register_commands
from ott.models import ProcessingMetrics


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mocks():
    """Build mock managers that cron and migrate commands drive."""
    radarr = Mock()
    sonarr = Mock()
    radarr.cron_cleanup.return_value = ProcessingMetrics(checked=5, cleaned=2)
    sonarr.cron_cleanup.return_value = ProcessingMetrics(checked=3, cleaned=1)
    radarr.migrate_ott_tags.return_value = {"total": 10, "tagged": 4}
    sonarr.migrate_ott_tags.return_value = {"total": 5, "tagged": 2}
    return radarr, sonarr


@pytest.fixture(autouse=True)
def reset_cli_app():
    """Typer accumulates registered commands across tests; reset before each."""
    app.registered_commands.clear()
    yield
    app.registered_commands.clear()


@pytest.fixture
def registered(mocks):
    """Register commands and return the (runner, radarr_mock, sonarr_mock) trio."""
    radarr, sonarr = mocks
    config = Mock()
    config.get = Mock(side_effect=lambda k, d=None: d)
    fastapi_app = Mock()
    register_commands(lambda: radarr, lambda: sonarr, fastapi_app, config)
    return radarr, sonarr


class TestCronCommand:
    def test_cron_triggers_both_managers(self, runner, registered):
        radarr, sonarr = registered
        result = runner.invoke(app, ["cron"])
        assert result.exit_code == 0
        assert radarr.cron_cleanup.called
        assert sonarr.cron_cleanup.called


class TestMigrateOttTagsCommand:
    def test_radarr_only(self, runner, registered):
        radarr, sonarr = registered
        result = runner.invoke(app, ["migrate-ott-tags", "radarr"])
        assert result.exit_code == 0
        radarr.migrate_ott_tags.assert_called_once_with(unmonitor=False)
        assert not sonarr.migrate_ott_tags.called

    def test_sonarr_only(self, runner, registered):
        radarr, sonarr = registered
        result = runner.invoke(app, ["migrate-ott-tags", "sonarr"])
        assert result.exit_code == 0
        sonarr.migrate_ott_tags.assert_called_once_with(unmonitor=False)
        assert not radarr.migrate_ott_tags.called

    def test_all_both_services(self, runner, registered):
        radarr, sonarr = registered
        result = runner.invoke(app, ["migrate-ott-tags", "all"])
        assert result.exit_code == 0
        assert radarr.migrate_ott_tags.called
        assert sonarr.migrate_ott_tags.called

    def test_unmonitor_flag_passes_through(self, runner, registered):
        radarr, _ = registered
        result = runner.invoke(app, ["migrate-ott-tags", "radarr", "--unmonitor"])
        assert result.exit_code == 0
        radarr.migrate_ott_tags.assert_called_once_with(unmonitor=True)

    def test_unknown_service_is_noop(self, runner, registered):
        """Unknown service names don't match radarr/sonarr/all, so nothing runs.
        Command still exits 0 (no validation errors, just a silent noop)."""
        radarr, sonarr = registered
        result = runner.invoke(app, ["migrate-ott-tags", "plex"])
        assert result.exit_code == 0
        assert not radarr.migrate_ott_tags.called
        assert not sonarr.migrate_ott_tags.called


class TestServerCommand:
    def test_server_command_invokes_uvicorn(self, runner, registered, monkeypatch):
        """server command calls uvicorn.run with the FastAPI app."""
        fake_uvicorn_run = Mock()
        monkeypatch.setattr("ott.cli.commands.uvicorn.run", fake_uvicorn_run)
        result = runner.invoke(app, ["server", "--port", "9999"])
        assert result.exit_code == 0
        assert fake_uvicorn_run.called
        _, kwargs = fake_uvicorn_run.call_args
        assert kwargs["port"] == 9999
