"""Lifespan management and dependency injection for the OpenSWMM MCP server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.session import SessionManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


@lifespan
async def server_lifespan(server) -> AsyncIterator[dict]:
    """FastMCP lifespan handler: bootstrap shared resources and tear them down on shutdown.

    Yields a context dict containing:
        session_manager: :class:`SessionManager` shared across all tool calls.
        settings: :class:`ServerSettings` loaded from the environment.
    """
    # 1. Build configuration from environment variables / .env
    settings = ServerSettings()

    # 2. Configure logging to match the requested level
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    logger.info("OpenSWMM MCP server starting (log_level=%s)", settings.log_level)

    # 3. Create the session manager that owns all SWMM simulation sessions
    session_manager = SessionManager(
        max_sessions=settings.max_sessions,
        working_dir=settings.working_dir,
    )

    # 3b. Create the managers that own interactive gym envs and
    #     background optimization jobs (gym_env_* / gym_*_job tools)
    from openswmm_mcp.gym_support.envs import EnvManager
    from openswmm_mcp.gym_support.jobs import JobManager

    env_manager = EnvManager()
    job_manager = JobManager()

    # 4. Yield the context dict so tools can access shared state
    try:
        yield {
            "session_manager": session_manager,
            "settings": settings,
            "env_manager": env_manager,
            "job_manager": job_manager,
        }
    finally:
        # 5. Cleanup on shutdown
        logger.info("OpenSWMM MCP server shutting down -- cleaning up sessions")
        job_manager.shutdown()
        env_manager.close_all()
        await session_manager.cleanup_all()


# ---------------------------------------------------------------------------
# Helper functions for extracting dependencies inside tool handlers
# ---------------------------------------------------------------------------


def get_session_manager(ctx: Context) -> SessionManager:
    """Extract the :class:`SessionManager` from the FastMCP lifespan context.

    Parameters
    ----------
    ctx:
        The FastMCP :class:`Context` injected into a tool handler.

    Returns
    -------
    SessionManager
        The shared session manager instance.

    Raises
    ------
    ToolError
        If the session manager is not available in the context.
    """
    try:
        return ctx.lifespan_context["session_manager"]
    except (KeyError, TypeError) as exc:
        raise ToolError(
            "Session manager is not available. The server may not have started correctly."
        ) from exc


def get_settings(ctx: Context) -> ServerSettings:
    """Extract :class:`ServerSettings` from the FastMCP lifespan context.

    Parameters
    ----------
    ctx:
        The FastMCP :class:`Context` injected into a tool handler.

    Returns
    -------
    ServerSettings
        The server configuration loaded at startup.

    Raises
    ------
    ToolError
        If settings are not available in the context.
    """
    try:
        return ctx.lifespan_context["settings"]
    except (KeyError, TypeError) as exc:
        raise ToolError(
            "Server settings are not available. The server may not have started correctly."
        ) from exc


def get_env_manager(ctx: Context):
    """Extract the gym C{EnvManager} from the FastMCP lifespan context.

    @param ctx: The FastMCP L{Context} injected into a tool handler.
    @type ctx: L{Context}
    @return: The shared interactive-env manager.
    @rtype: L{EnvManager<openswmm_mcp.gym_support.envs.EnvManager>}
    @raise ToolError: If the env manager is not available in the context.
    """
    try:
        return ctx.lifespan_context["env_manager"]
    except (KeyError, TypeError) as exc:
        raise ToolError(
            "Gym env manager is not available. The server may not have started correctly."
        ) from exc


def get_job_manager(ctx: Context):
    """Extract the gym C{JobManager} from the FastMCP lifespan context.

    @param ctx: The FastMCP L{Context} injected into a tool handler.
    @type ctx: L{Context}
    @return: The shared background-optimization job manager.
    @rtype: L{JobManager<openswmm_mcp.gym_support.jobs.JobManager>}
    @raise ToolError: If the job manager is not available in the context.
    """
    try:
        return ctx.lifespan_context["job_manager"]
    except (KeyError, TypeError) as exc:
        raise ToolError(
            "Gym job manager is not available. The server may not have started correctly."
        ) from exc


def require_state(session, *valid_states: str) -> None:
    """Assert that *session* is in one of *valid_states*.

    Parameters
    ----------
    session:
        A session object that exposes a ``.state`` attribute.
    *valid_states:
        One or more acceptable state strings (e.g. ``"running"``, ``"paused"``).

    Raises
    ------
    ToolError
        If ``session.state`` is not among the accepted states.
    """
    if session.state not in valid_states:
        allowed = ", ".join(f"'{s}'" for s in valid_states)
        raise ToolError(
            f"Session is in state '{session.state}', but this action requires one of: {allowed}."
        )


def require_new_engine(session, feature: str) -> None:
    """Assert that *session* uses the new ``openswmm`` engine backend.

    Tools that depend on new-engine-only APIs (ModelBuilder, ModelEditor,
    Controls, Inflows, Infrastructure, Quality, Spatial, Tables, Statistics,
    OutputReader, GeoPackage) call this guard to fail fast on legacy
    sessions with a clear error code instead of an obscure ``AttributeError``
    from the backend.

    Parameters
    ----------
    session:
        A session object exposing ``engine_kind``.
    feature:
        Human-readable name of the feature being requested, used in the
        error message (e.g. ``"ModelBuilder"``, ``"Spatial coordinates"``).

    Raises
    ------
    ToolError
        With code :data:`~openswmm_mcp.errors.ErrorCode.NOT_SUPPORTED` when
        the session was created with ``engine='legacy'``.
    """
    from openswmm_mcp.errors import ErrorCode

    kind = getattr(session, "engine_kind", "openswmm")
    if kind != "openswmm":
        raise ToolError(
            f"[{ErrorCode.NOT_SUPPORTED}] {feature} requires the new openswmm "
            f"engine; this session was opened with engine='{kind}'. "
            f"Re-open the model with engine='openswmm' (the default) to use "
            f"spatial/geometry and other new-engine tools."
        )


def require_gymnasium(feature: str = "This tool") -> None:
    """Assert that the optional C{openswmm.gymnasium} package is importable.

    Gym tool modules import C{openswmm_gymnasium} lazily so the server
    starts cleanly without the C{gym} extra; this guard converts the
    eventual C{ImportError} into an actionable C{ToolError} instead.

    @param feature: Human-readable name of the feature being requested,
        used in the error message (e.g. C{"gym_run_episode"}).
    @type feature: str
    @raise ToolError: With code
        L{ErrorCode.DEPENDENCY_MISSING<openswmm_mcp.errors.ErrorCode>}
        when C{openswmm_gymnasium} cannot be imported.
    @return: C{None}
    @rtype: C{None}
    @author: Caleb Buahin
    """
    from openswmm_mcp.errors import ErrorCode

    try:
        import openswmm_gymnasium  # noqa: F401
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.DEPENDENCY_MISSING}] {feature} requires the optional "
            "openswmm.gymnasium package. Install it with: "
            "pip install 'openswmm.mcp[gym]'"
        ) from exc
