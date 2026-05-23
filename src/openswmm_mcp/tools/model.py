"""Model tools: title / userflag / options / CRS access.

The Python ``ModelBuilder`` (BUILDING state) and ``Solver`` (OPENED state
and later) both expose:

* [TITLE] section accessors: get_title_count / get_title_line /
  add_title_line / set_title / clear_title.
* SWMM options: get_option / set_option (string-keyed) and
  get_option_ext / set_option_ext for extension options.
* User flags: get/set_userflag_{bool,int,real}, application-defined
  metadata persisted alongside the model.
* CRS string: get_crs.

This module surfaces all of those via the ``model`` MCP namespace. State
handling: works against the ModelBuilder for BUILDING sessions, against
the Solver for OPENED / INITIALIZED / RUNNING / ENDED.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

model_mcp = FastMCP("model")


async def _get_target(ctx: Context, session_id: str) -> tuple[SimSession, Any]:
    """Return ``(session, target)`` where target is ModelBuilder or Solver.

    Use for methods that exist on BOTH classes (options, CRS).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Model (title / options / userflags)")
    if session.state == "closed":
        raise ToolError(f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is closed.")
    if session.state == "building":
        if session.model_builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no ModelBuilder attached."
            )
        return session, session.model_builder
    return session, session.solver


async def _get_builder(ctx: Context, session_id: str) -> tuple[SimSession, Any]:
    """Return ``(session, model_builder)``; requires BUILDING state.

    Use for methods that only exist on ModelBuilder: title section,
    user flags, [PLUGINS] / [FILES] editors, write_with_plugin.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Model (title / userflags / plugins / files)")
    if session.state != "building":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{session.state}'; this tool requires 'building'. Create the "
            f"model via building.create_model first."
        )
    if session.model_builder is None:
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no ModelBuilder attached."
        )
    return session, session.model_builder


# ===========================================================================
# [TITLE] section
# ===========================================================================


@model_mcp.tool()
async def get_title_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of lines in the C{[TITLE]} section (BUILDING)."""
    _, target = await _get_builder(ctx, session_id)
    n = await asyncio.to_thread(target.get_title_count)
    return {"session_id": session_id, "count": n}


@model_mcp.tool()
async def get_title_line(ctx: Context, session_id: str = "default", line_index: int = 0) -> dict:
    """Return the I{line_index}-th line of the [TITLE] section (BUILDING)."""
    _, target = await _get_builder(ctx, session_id)
    text = await asyncio.to_thread(target.get_title_line, line_index)
    return {
        "session_id": session_id,
        "line_index": line_index,
        "text": text,
    }


@model_mcp.tool()
async def get_title(ctx: Context, session_id: str = "default") -> dict:
    """Return the full [TITLE] section as a list of lines (BUILDING).

    Convenience wrapper that batches get_title_count + N x get_title_line.
    """
    _, target = await _get_builder(ctx, session_id)

    def _read_all() -> list[str]:
        n = target.get_title_count()
        return [target.get_title_line(i) for i in range(n)]

    lines = await asyncio.to_thread(_read_all)
    return {
        "session_id": session_id,
        "count": len(lines),
        "lines": lines,
    }


@model_mcp.tool()
async def add_title_line(ctx: Context, session_id: str = "default", text: str = "") -> dict:
    """Append a line to the [TITLE] section."""
    if not text:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] text must not be empty.")
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(target.add_title_line, text)
    return {"status": "ok", "session_id": session_id, "added": text}


@model_mcp.tool()
async def set_title(ctx: Context, session_id: str = "default", text: str = "") -> dict:
    """Replace all [TITLE] lines with new text (newline-separated, BUILDING)."""
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(target.set_title, text)
    return {"status": "ok", "session_id": session_id}


@model_mcp.tool()
async def clear_title(ctx: Context, session_id: str = "default") -> dict:
    """Remove every line from the [TITLE] section (BUILDING)."""
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(target.clear_title)
    return {"status": "ok", "session_id": session_id, "remaining": 0}


# ===========================================================================
# [OPTIONS] section
# ===========================================================================


@model_mcp.tool()
async def get_option(ctx: Context, session_id: str = "default", key: str = "") -> dict:
    """Return a SWMM option value as a string.

    Example keys: ``FLOW_UNITS``, ``FLOW_ROUTING``, ``ROUTING_STEP``,
    ``REPORT_STEP``, ``SURCHARGE_METHOD``. Consult the SWMM 5 reference
    for the full key list.
    """
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] key must not be empty.")
    _, target = await _get_target(ctx, session_id)
    value = await asyncio.to_thread(target.get_option, key)
    return {"session_id": session_id, "key": key, "value": value}


@model_mcp.tool()
async def set_option(
    ctx: Context,
    session_id: str = "default",
    key: str = "",
    value: str = "",
) -> dict:
    """Set a SWMM option (string key, string value)."""
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] key must not be empty.")
    _, target = await _get_target(ctx, session_id)
    await asyncio.to_thread(target.set_option, key, value)
    return {"status": "ok", "session_id": session_id, "key": key, "value": value}


@model_mcp.tool()
async def get_option_ext(ctx: Context, session_id: str = "default", key: str = "") -> dict:
    """Return an extension option value (unknown to base SWMM)."""
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] key must not be empty.")
    _, target = await _get_target(ctx, session_id)
    value = await asyncio.to_thread(target.get_option_ext, key)
    return {"session_id": session_id, "key": key, "value": value}


@model_mcp.tool()
async def set_option_ext(
    ctx: Context,
    session_id: str = "default",
    key: str = "",
    value: str = "",
) -> dict:
    """Set an extension option."""
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] key must not be empty.")
    _, target = await _get_target(ctx, session_id)
    await asyncio.to_thread(target.set_option_ext, key, value)
    return {"status": "ok", "session_id": session_id, "key": key, "value": value}


@model_mcp.tool()
async def get_crs(ctx: Context, session_id: str = "default") -> dict:
    """Return the model's coordinate reference system string."""
    _, target = await _get_target(ctx, session_id)
    crs = await asyncio.to_thread(target.get_crs)
    return {"session_id": session_id, "crs": crs}


# ===========================================================================
# User flags
# ===========================================================================


async def _userflag_get(ctx, session_id, name, method, value_key) -> dict:
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] name must not be empty.")
    _, target = await _get_builder(ctx, session_id)
    v = await asyncio.to_thread(getattr(target, method), name)
    return {
        "session_id": session_id,
        "name": name,
        value_key: v,
    }


async def _userflag_set(ctx, session_id, name, value, method, value_key) -> dict:
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] name must not be empty.")
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(getattr(target, method), name, value)
    return {
        "status": "ok",
        "session_id": session_id,
        "name": name,
        value_key: value,
    }


@model_mcp.tool()
async def get_userflag_bool(ctx: Context, session_id: str = "default", name: str = "") -> dict:
    """Return a boolean user flag (application-defined metadata)."""
    return await _userflag_get(ctx, session_id, name, "get_userflag_bool", "value")


@model_mcp.tool()
async def set_userflag_bool(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    value: bool = False,
) -> dict:
    """Set a boolean user flag."""
    return await _userflag_set(ctx, session_id, name, bool(value), "set_userflag_bool", "value")


@model_mcp.tool()
async def get_userflag_int(ctx: Context, session_id: str = "default", name: str = "") -> dict:
    """Return an integer user flag."""
    return await _userflag_get(ctx, session_id, name, "get_userflag_int", "value")


@model_mcp.tool()
async def set_userflag_int(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    value: int = 0,
) -> dict:
    """Set an integer user flag."""
    return await _userflag_set(ctx, session_id, name, int(value), "set_userflag_int", "value")


@model_mcp.tool()
async def get_userflag_real(ctx: Context, session_id: str = "default", name: str = "") -> dict:
    """Return a real-valued user flag."""
    return await _userflag_get(ctx, session_id, name, "get_userflag_real", "value")


@model_mcp.tool()
async def set_userflag_real(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    value: float = 0.0,
) -> dict:
    """Set a real-valued user flag."""
    return await _userflag_set(ctx, session_id, name, float(value), "set_userflag_real", "value")


# ===========================================================================
# [PLUGINS] + [FILES] section editors
# ===========================================================================


@model_mcp.tool()
async def plugins_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of [PLUGINS] entries on the engine."""
    _, target = await _get_builder(ctx, session_id)
    n = await asyncio.to_thread(target.plugins_count)
    return {"session_id": session_id, "count": n}


@model_mcp.tool()
async def plugin_get(ctx: Context, session_id: str = "default", index: int = 0) -> dict:
    """Return the (path, args) of the I{index}-th plugin entry."""
    _, target = await _get_builder(ctx, session_id)
    path, args = await asyncio.to_thread(target.plugin_get, index)
    return {
        "session_id": session_id,
        "index": index,
        "path": path,
        "args": args,
    }


@model_mcp.tool()
async def plugin_set(
    ctx: Context,
    session_id: str = "default",
    path_or_id: str = "",
    args: str = "",
) -> dict:
    """Add or update a plugin entry.

    ``path_or_id`` is the library path, plugin id, or ``id:version`` string.
    """
    if not path_or_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] path_or_id must not be empty.")
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(target.plugin_set, path_or_id, args)
    return {
        "status": "ok",
        "session_id": session_id,
        "path_or_id": path_or_id,
        "args": args,
    }


@model_mcp.tool()
async def plugin_remove(ctx: Context, session_id: str = "default", path_or_id: str = "") -> dict:
    """Remove the plugin entry matching ``path_or_id``."""
    if not path_or_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] path_or_id must not be empty.")
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(target.plugin_remove, path_or_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "removed": path_or_id,
    }


@model_mcp.tool()
async def files_get(ctx: Context, session_id: str = "default", key: str = "") -> dict:
    """Return the path / value for a [FILES] section field.

    Common keys: ``RAINFALL_PATH``, ``RUNOFF_PATH``, ``RDII_PATH``,
    ``HOTSTART_USE_PATH``, ``HOTSTART_SAVE_PATH``.
    """
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] key must not be empty.")
    _, target = await _get_builder(ctx, session_id)
    value = await asyncio.to_thread(target.files_get, key)
    return {"session_id": session_id, "key": key, "value": value}


@model_mcp.tool()
async def files_set(
    ctx: Context,
    session_id: str = "default",
    key: str = "",
    value: str = "",
) -> dict:
    """Set a [FILES] section field. Empty value clears the field."""
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] key must not be empty.")
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(target.files_set, key, value)
    return {"status": "ok", "session_id": session_id, "key": key, "value": value}


@model_mcp.tool()
async def write_with_plugin(
    ctx: Context,
    session_id: str = "default",
    path: str = "",
    output_plugin_id: str = "",
) -> dict:
    """Write the model to disk via an output plugin (or built-in writer).

    Pass an empty ``output_plugin_id`` (the default) to use the built-in
    `.inp` writer. Non-empty values select a registered output plugin
    (e.g. GeoPackage / HDF5).
    """
    if not path:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] path must not be empty.")
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(target.write_with_plugin, path, output_plugin_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "path": path,
        "output_plugin_id": output_plugin_id,
    }
