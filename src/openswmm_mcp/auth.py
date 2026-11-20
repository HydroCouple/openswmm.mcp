"""OAuth / JWT authentication for the OpenSWMM MCP HTTP transport.

Authentication strategy
-----------------------
* **stdio transport** -- No authentication is applied.  The server runs as a
  local subprocess and inherits the caller's OS-level identity, so adding
  token verification would only add friction with no security benefit.

* **http / sse transport** -- Three schemes are supported, selected by the
  ``ServerSettings`` fields ``jwt_jwks_url`` and ``oauth_issuer``:

  1. *JWT only* (``jwt_jwks_url`` is set): incoming requests must carry a
     Bearer token whose signature is verified against the JSON Web Key Set
     at the given URL.
  2. *OAuth only* (``oauth_issuer`` is set): a full OAuth 2.0 authorization
     code / client-credentials flow is used, with the issuer URL serving as
     the OpenID Connect discovery root.
  3. *Both* (both fields set): the two providers are combined so that a
     request succeeds if *either* verifier accepts the credential.
  4. *Neither* set: the server starts **without** authentication.  This is
     useful for local development or environments that handle auth at an
     upstream reverse-proxy layer.

The ``fastmcp[auth]`` extras package must be installed for any of the
authenticated modes.  If the extras are missing and auth is requested, the
function raises an ``ImportError`` with an actionable message.
"""

from __future__ import annotations

import logging
from typing import Any

from openswmm_mcp.config import ServerSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy, guarded imports -- the auth extras may not be installed.
# ---------------------------------------------------------------------------

_HAS_AUTH_EXTRAS = False

# The functional providers below are what ``create_auth`` actually builds, so
# their presence -- not the legacy ``BearerAuthProvider`` symbol, which fastmcp
# 3.x removed -- determines whether the auth extras are installed.
try:
    from fastmcp.server.auth import JWTVerifier as _JWTVerifier  # type: ignore[import-untyped]

    _HAS_AUTH_EXTRAS = True
except ImportError:
    _JWTVerifier = None  # type: ignore[assignment,misc]

try:
    from fastmcp.server.auth import MultiAuth as _MultiAuth  # type: ignore[import-untyped]
except ImportError:
    _MultiAuth = None  # type: ignore[assignment,misc]

# Legacy alias: pre-3.x fastmcp exposed JWT bearer verification as
# ``BearerAuthProvider``; newer versions renamed it to ``JWTVerifier``.  Keep
# the name importable and, on old fastmcp lacking ``JWTVerifier``, fall back to
# it -- without gating the extras flag on a symbol that may not exist.
try:
    from fastmcp.server.auth import (
        BearerAuthProvider as _BearerAuthProvider,  # type: ignore[import-untyped]
    )

    if _JWTVerifier is None:
        _JWTVerifier = _BearerAuthProvider  # type: ignore[assignment,misc]
    _HAS_AUTH_EXTRAS = True
except ImportError:
    _BearerAuthProvider = _JWTVerifier  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _ensure_auth_extras(feature: str) -> None:
    """Raise a helpful ``ImportError`` if the auth extras are not installed."""
    if not _HAS_AUTH_EXTRAS:
        raise ImportError(
            f"The '{feature}' auth feature requires the fastmcp auth extras. "
            "Install them with:  pip install 'fastmcp[auth]'"
        )


def create_auth(settings: ServerSettings) -> Any | None:
    """Build an authentication provider based on *settings*, or return ``None``.

    Parameters
    ----------
    settings:
        The :class:`~openswmm_mcp.config.ServerSettings` loaded at startup.

    Returns
    -------
    An auth provider instance understood by :class:`fastmcp.FastMCP`, or
    ``None`` if no authentication should be applied.

    Raises
    ------
    ImportError
        If authentication is requested but the ``fastmcp[auth]`` extras are
        not installed.
    """
    # stdio is always unauthenticated -- it is a local subprocess.
    if settings.transport == "stdio":
        logger.debug("Transport is stdio; skipping authentication setup.")
        return None

    jwt_provider: Any | None = None
    oauth_provider: Any | None = None

    # --- JWT verification via an explicit JWKS endpoint ---
    if settings.jwt_jwks_url:
        _ensure_auth_extras("JWT")
        logger.info("Configuring JWT verification (jwks_uri=%s)", settings.jwt_jwks_url)
        jwt_provider = _JWTVerifier(  # type: ignore[misc]
            jwks_uri=settings.jwt_jwks_url,
            audience=settings.oauth_audience,
        )

    # --- OIDC issuer: validate JWTs against the issuer's JWKS ---
    # fastmcp 3.x's OAuthProvider is a full OAuth *server* base; validating
    # bearer tokens minted by an external issuer is a JWTVerifier keyed on that
    # issuer's JWKS.  fastmcp does not perform OIDC discovery, so derive the
    # conventional JWKS path (set OPENSWMM_MCP_JWT_JWKS_URL explicitly when the
    # issuer publishes its keys elsewhere).
    if settings.oauth_issuer:
        _ensure_auth_extras("OAuth")
        jwks_uri = f"{settings.oauth_issuer.rstrip('/')}/.well-known/jwks.json"
        logger.info("Configuring OIDC verification (issuer=%s)", settings.oauth_issuer)
        oauth_provider = _JWTVerifier(  # type: ignore[misc]
            jwks_uri=jwks_uri,
            issuer=settings.oauth_issuer,
            audience=settings.oauth_audience,
        )

    # --- Combine verifiers when both are configured ---
    if jwt_provider and oauth_provider:
        _ensure_auth_extras("MultiAuth")
        if _MultiAuth is None:
            # fastmcp version does not expose MultiAuth; fall back to the
            # issuer verifier alone and log a warning.
            logger.warning(
                "Both JWT and OIDC are configured, but fastmcp.server.auth.MultiAuth "
                "is not available in this version.  Falling back to the issuer verifier."
            )
            return oauth_provider
        logger.info("Combining JWT and OIDC verifiers into a MultiAuth provider.")
        return _MultiAuth(verifiers=[jwt_provider, oauth_provider])

    # Return whichever single provider is configured, or None.
    provider = jwt_provider or oauth_provider

    if provider is None and settings.transport in ("http", "sse"):
        logger.warning(
            "HTTP transport is active but no auth is configured. "
            "Set OPENSWMM_MCP_JWT_JWKS_URL or OPENSWMM_MCP_OAUTH_ISSUER "
            "to enable authentication, or ensure an upstream proxy handles it."
        )

    return provider
