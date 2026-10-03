"""Tests for openswmm_mcp.auth -- authentication provider creation."""

from __future__ import annotations

import pytest

from openswmm_mcp.auth import create_auth
from openswmm_mcp.config import ServerSettings

# ---------------------------------------------------------------------------
# Basic transport / config tests (no auth extras needed)
# ---------------------------------------------------------------------------


class TestAuthStdio:
    def test_stdio_returns_none(self):
        """stdio transport never uses authentication."""
        settings = ServerSettings(transport="stdio")
        assert create_auth(settings) is None

    def test_stdio_ignores_jwt_settings(self):
        """Even when JWT settings are provided, stdio still returns None."""
        settings = ServerSettings(
            transport="stdio",
            jwt_jwks_url="https://example.com/.well-known/jwks.json",
        )
        assert create_auth(settings) is None

    def test_stdio_ignores_oauth_settings(self):
        """Even when OAuth settings are provided, stdio still returns None."""
        settings = ServerSettings(
            transport="stdio",
            oauth_issuer="https://example.com",
            oauth_audience="openswmm",
        )
        assert create_auth(settings) is None


class TestAuthHttpNoConfig:
    def test_http_no_config_returns_none(self):
        """HTTP transport without any auth config returns None."""
        settings = ServerSettings(transport="http")
        assert create_auth(settings) is None

    def test_sse_no_config_returns_none(self):
        """SSE transport without any auth config returns None."""
        settings = ServerSettings(transport="sse")
        assert create_auth(settings) is None


# ---------------------------------------------------------------------------
# JWT / OAuth tests -- skip if fastmcp[auth] is not installed
# ---------------------------------------------------------------------------

try:
    import fastmcp.server.auth  # noqa: F401

    from openswmm_mcp.auth import _HAS_AUTH_EXTRAS

    _HAS_AUTH = _HAS_AUTH_EXTRAS
except ImportError:
    _HAS_AUTH = False

needs_auth_extras = pytest.mark.skipif(
    not _HAS_AUTH,
    reason="fastmcp[auth] extras not installed",
)


@needs_auth_extras
class TestAuthJWT:
    def test_http_jwt_returns_provider(self):
        """HTTP transport with jwt_jwks_url returns an auth provider."""
        settings = ServerSettings(
            transport="http",
            jwt_jwks_url="https://example.com/.well-known/jwks.json",
            oauth_audience="openswmm",
        )
        provider = create_auth(settings)
        assert provider is not None

    def test_sse_jwt_returns_provider(self):
        """SSE transport with jwt_jwks_url returns an auth provider."""
        settings = ServerSettings(
            transport="sse",
            jwt_jwks_url="https://example.com/.well-known/jwks.json",
        )
        provider = create_auth(settings)
        assert provider is not None


@needs_auth_extras
class TestAuthOAuth:
    def test_http_oauth_returns_provider(self):
        """HTTP transport with oauth_issuer returns an auth provider."""
        try:
            import fastmcp.server.auth  # noqa: F401
        except ImportError:
            pytest.skip("OAuthProvider not available in this fastmcp version")

        settings = ServerSettings(
            transport="http",
            oauth_issuer="https://accounts.example.com",
            oauth_audience="openswmm",
        )
        provider = create_auth(settings)
        assert provider is not None


@needs_auth_extras
class TestAuthImportError:
    def test_jwt_without_extras_raises(self, monkeypatch):
        """If auth extras are missing and JWT is requested, ImportError is raised."""
        import openswmm_mcp.auth as auth_module

        # Temporarily force the _HAS_AUTH_EXTRAS flag to False
        monkeypatch.setattr(auth_module, "_HAS_AUTH_EXTRAS", False)

        settings = ServerSettings(
            transport="http",
            jwt_jwks_url="https://example.com/.well-known/jwks.json",
        )
        with pytest.raises(ImportError, match="fastmcp"):
            create_auth(settings)
