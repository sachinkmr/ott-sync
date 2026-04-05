"""Tests for the FastAPI app's schema and pure-function endpoints.

Route-level tests for webhooks/telegram/cache require a full manager set-up
plus a real DB and are covered in integration tests under test_webhooks.py.
This file covers the things that don't need that infra: callback-data
parsing, local-client allowlist, and the OpenAPI schema surface.
"""

from unittest.mock import Mock

from fastapi import Request
from fastapi.testclient import TestClient

from ott.api.app import app
from ott.api.cache import _is_local_address, _require_local_client
from ott.api.models import TelegramCallbackAction, parse_callback_action


# ---------------------------------------------------------------------------
# Telegram callback_data validation
# ---------------------------------------------------------------------------

class TestParseCallbackAction:
    """Tests for parse_callback_action() and TelegramCallbackAction."""

    def test_valid_override(self):
        parsed = parse_callback_action("override:movie:123")
        assert parsed == TelegramCallbackAction(action="override", target="movie", item_id=123)

    def test_valid_approve(self):
        parsed = parse_callback_action("approve:series:456")
        assert parsed.action == "approve"
        assert parsed.target == "series"
        assert parsed.item_id == 456

    def test_valid_anime(self):
        parsed = parse_callback_action("anime:confirm:789")
        assert parsed.action == "anime"
        assert parsed.target == "confirm"
        assert parsed.item_id == 789

    def test_extra_colons_in_id(self):
        """`override:movie:123:extra` should fail because int() rejects the id."""
        assert parse_callback_action("override:movie:123:extra") is None

    def test_empty_string(self):
        assert parse_callback_action("") is None

    def test_none(self):
        assert parse_callback_action(None) is None

    def test_only_two_parts(self):
        assert parse_callback_action("override:movie") is None

    def test_non_integer_id(self):
        assert parse_callback_action("override:movie:NaN") is None

    def test_negative_id_rejected(self):
        """Pydantic's ge=0 constraint kicks in."""
        assert parse_callback_action("override:movie:-5") is None


# ---------------------------------------------------------------------------
# Cache-route localhost allowlist
# ---------------------------------------------------------------------------

class TestIsLocalAddress:
    """Tests for _is_local_address() allowlist logic."""

    def test_loopback_ipv4(self):
        assert _is_local_address("127.0.0.1")
        assert _is_local_address("127.0.0.2")

    def test_loopback_hostname(self):
        assert _is_local_address("localhost")

    def test_loopback_ipv6(self):
        assert _is_local_address("::1")

    def test_rfc1918_10(self):
        assert _is_local_address("10.0.0.1")

    def test_rfc1918_192168(self):
        assert _is_local_address("192.168.1.1")

    def test_rfc1918_172_in_range(self):
        assert _is_local_address("172.16.0.1")
        assert _is_local_address("172.20.0.1")
        assert _is_local_address("172.31.255.254")

    def test_rfc1918_172_outside_range_rejected(self):
        """Regression: the old prefix-match wrongly allowed 172.0-15 and 172.32+."""
        assert not _is_local_address("172.0.0.1")
        assert not _is_local_address("172.15.0.1")
        assert not _is_local_address("172.32.0.1")
        assert not _is_local_address("172.217.16.1")  # Google public

    def test_cgnat_rejected(self):
        """100.64.0.0/10 is carrier-grade NAT, not a trusted local network."""
        assert not _is_local_address("100.64.0.1")

    def test_link_local_ipv4_allowed(self):
        """Python's ipaddress treats 169.254/16 as private (DHCP autoconfig).
        Link-local addresses can't cross network boundaries so they are as
        safe as RFC-1918 for a locally-restricted endpoint."""
        assert _is_local_address("169.254.0.1")

    def test_public_ipv4_rejected(self):
        assert not _is_local_address("8.8.8.8")
        assert not _is_local_address("1.1.1.1")

    def test_ipv6_unique_local(self):
        assert _is_local_address("fd00::1")

    def test_invalid_input(self):
        assert not _is_local_address(None)
        assert not _is_local_address("")
        assert not _is_local_address("not-an-ip")


class TestRequireLocalClient:
    """_require_local_client raises 403 for non-local callers, including None."""

    def _req(self, host: str | None) -> Request:
        """Build a Request-like object with a .client tuple."""
        req = Mock(spec=Request)
        if host is None:
            req.client = None
        else:
            client = Mock()
            client.host = host
            req.client = client
        return req

    def test_allows_loopback(self):
        assert _require_local_client(self._req("127.0.0.1"), "test") == "127.0.0.1"

    def test_allows_rfc1918(self):
        assert _require_local_client(self._req("192.168.1.5"), "test") == "192.168.1.5"

    def test_rejects_public(self):
        from fastapi import HTTPException
        import pytest
        with pytest.raises(HTTPException) as exc_info:
            _require_local_client(self._req("8.8.8.8"), "test")
        assert exc_info.value.status_code == 403

    def test_rejects_none_client(self):
        """request.client = None (proxy/test client) is treated as not-local."""
        from fastapi import HTTPException
        import pytest
        with pytest.raises(HTTPException) as exc_info:
            _require_local_client(self._req(None), "test")
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------

class TestOpenAPIDocumentation:
    """The OpenAPI schema is published and lists our endpoints."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_openapi_json(self):
        response = self.client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "info" in data
        assert "paths" in data

    def test_docs_ui(self):
        response = self.client.get("/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower() or "openapi" in response.text.lower()
