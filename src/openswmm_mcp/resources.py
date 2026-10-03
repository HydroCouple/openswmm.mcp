"""MCP resources: the engine catalog and live session summaries (``swmm://`` URIs)."""

from __future__ import annotations

import json

from fastmcp import Context, FastMCP

from openswmm_mcp import catalog as cat
from openswmm_mcp.dependencies import get_session, get_session_manager
from openswmm_mcp.tools.model import summary


async def catalog_index() -> str:
    """Every target the engine exposes, with its class and one-line description."""
    return json.dumps(
        {name: {"class": e["class"], "doc": e["doc"][:200]} for name, e in cat.targets().items()},
        indent=1,
    )


async def catalog_target(target: str) -> str:
    """Full catalog entries (fields and methods) for one target, e.g. ``node`` or ``forcing``."""
    cat.require_target(target)
    return json.dumps(
        {"target": cat.targets()[target], "members": list(cat.members(target).values())}, indent=1
    )


async def sessions(ctx: Context) -> str:
    """All open sessions with their state and input file."""
    return json.dumps(
        [
            {"session_id": s.session_id, "state": s.state, "inp": s.inp_path}
            for s in await get_session_manager(ctx).sessions()
        ],
        indent=1,
    )


async def session_summary(session_id: str, ctx: Context) -> str:
    """Counts, key options, time window and file paths for one session."""
    session = await get_session(ctx, session_id)
    return json.dumps(await session.call(summary, session), indent=1)


def register_resources(server: FastMCP) -> None:
    """Register on the root server so resources share the tools' session manager."""
    server.resource("swmm://catalog")(catalog_index)
    server.resource("swmm://catalog/{target}")(catalog_target)
    server.resource("swmm://sessions")(sessions)
    server.resource("swmm://session/{session_id}/summary")(session_summary)
