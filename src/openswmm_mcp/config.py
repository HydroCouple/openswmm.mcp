"""Server configuration via environment variables (OPENSWMM_MCP_ prefix)."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
    """OpenSWMM-MCP server settings.

    Every field can be overridden with an environment variable prefixed by
    ``OPENSWMM_MCP_``.  For example, ``OPENSWMM_MCP_MAX_SESSIONS=10``.
    """

    working_dir: str = "./"
    max_sessions: int = 5
    transport: str = "stdio"  # "stdio" | "http" | "sse"
    http_port: int = 8080
    log_level: str = "INFO"

    # Comma-separated tool sets to register: "core" (always), "gym".
    toolsets: str = "core"
    # Register the ``run_python`` tool (Python with the session's Solver in
    # scope). Honoured only on the stdio transport; never enable it on a
    # server reachable by others.
    enable_python: bool = False

    # Optional OAuth / JWT fields for authenticated transports
    oauth_issuer: str | None = None
    oauth_audience: str | None = None
    jwt_jwks_url: str | None = None

    model_config = {"env_prefix": "OPENSWMM_MCP_"}


settings = ServerSettings()
