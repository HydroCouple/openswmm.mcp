"""Lifespan management and dependency lookup for the OpenSWMM MCP server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from fastmcp.server.lifespan import lifespan

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SessionManager, SimSession

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class _RefusalsAsOneLine(logging.Filter):
    """Log a tool refusal (a ToolError) as one line instead of a traceback.

    A refusal, such as a missing argument or a lifecycle phase, is an answer the
    client already receives. FastMCP logs it with ``logger.exception``, which
    renders a full traceback for every refused call.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, ToolError):
            record.msg, record.args = f"{record.getMessage()}: {exc}", ()
            record.exc_info, record.exc_text = None, None
            record.levelno, record.levelname = logging.INFO, "INFO"
        return True


@lifespan
async def server_lifespan(server) -> AsyncIterator[dict]:
    """Create the shared session manager (and gym managers when that toolset is on)."""
    settings = ServerSettings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    tool_log = logging.getLogger("fastmcp.server.server")
    if not any(isinstance(f, _RefusalsAsOneLine) for f in tool_log.filters):
        tool_log.addFilter(_RefusalsAsOneLine())
    context: dict[str, Any] = {
        "settings": settings,
        "session_manager": SessionManager(settings.max_sessions, settings.working_dir),
    }
    if "gym" in toolsets(settings):
        from openswmm_gymnasium.spec.envs import EnvManager

        from openswmm_mcp.gym_support.jobs import JobManager

        context["env_manager"] = EnvManager()
        context["job_manager"] = JobManager()
    try:
        yield context
    finally:
        logger.info("OpenSWMM MCP server shutting down -- cleaning up sessions")
        if "job_manager" in context:
            context["job_manager"].shutdown()
            context["env_manager"].close_all()
        await context["session_manager"].cleanup_all()


def toolsets(settings: ServerSettings) -> set[str]:
    return {"core"} | {t.strip() for t in settings.toolsets.split(",") if t.strip()}


def _lifespan_item(ctx: Context, key: str) -> Any:
    try:
        return ctx.lifespan_context[key]
    except (KeyError, TypeError) as exc:
        raise ToolError(
            f"[{ErrorCode.NOT_SUPPORTED}] '{key}' is not available; the server "
            "was started without the toolset that provides it."
        ) from exc


def get_session_manager(ctx: Context) -> SessionManager:
    return _lifespan_item(ctx, "session_manager")


def get_settings(ctx: Context) -> ServerSettings:
    return _lifespan_item(ctx, "settings")


def get_env_manager(ctx: Context):
    return _lifespan_item(ctx, "env_manager")


def get_job_manager(ctx: Context):
    return _lifespan_item(ctx, "job_manager")


async def get_session(ctx: Context, session_id: str) -> SimSession:
    return await get_session_manager(ctx).get(session_id)


def require_gymnasium(feature: str = "This tool") -> None:
    """Raise an actionable error when the optional gymnasium package is missing."""
    try:
        import openswmm_gymnasium.spec  # noqa: F401
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.DEPENDENCY_MISSING}] {feature} requires the optional "
            "openswmm.gymnasium package. Install it with: pip install 'openswmm.mcp[gym]'"
        ) from exc
