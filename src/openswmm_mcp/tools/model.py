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
from datetime import datetime
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


def _options_mapping(target):
    """Return the v1 ``options`` MutableMapping when present, else ``None``.

    ``ModelBuilder`` keeps the v0 ``get_option`` / ``set_option`` method
    pair (no ``options`` mapping attribute), while ``Solver`` (and the
    legacy adapter shim) expose ``options`` as a mapping.  Tools that
    need to work against both branch on this helper.
    """
    options = getattr(target, "options", None)
    if options is not None and hasattr(options, "__getitem__"):
        return options
    return None


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

    def _read() -> str:
        options = _options_mapping(target)
        if options is not None:
            return options[key]
        return target.get_option(key)

    value = await asyncio.to_thread(_read)
    return {"session_id": session_id, "key": key, "value": value}


@model_mcp.tool()
async def set_option(
    ctx: Context,
    session_id: str = "default",
    key: str = "",
    value: str = "",
) -> dict:
    """Set a SWMM option (string key, string value).

    Accepts any key the engine's option API recognizes, including the
    ``FV_*`` family that configures the explicit finite-volume solver
    (``FLOW_ROUTING`` = ``FV``): ``FV_CELL_LENGTH``, ``FV_MIN_CELLS``,
    ``FV_CFL``, ``FV_RIEMANN``, ``FV_ORDER``, ``FV_LIMITER``,
    ``FV_SCALAR_SCHEME``, ``FV_TIME_INTEGRATION``, ``FV_SLOT_CELERITY``,
    ``FV_DISPERSION``, ``FV_STRUCTURE_COUPLING``, ``FV_COMPACTION``,
    ``FV_BACKEND`` and ``FV_MIN_PARALLEL_CELLS``.  These are inert under
    the other routing models rather than rejected, so they can be set
    before ``FLOW_ROUTING`` is switched.

    Note that finite-volume routing needs a resolved mesh to reproduce
    dynamic-wave peak flows -- set ``FV_CELL_LENGTH`` rather than leaving
    it at the one-cell-per-conduit default when peaks matter.
    """
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] key must not be empty.")
    _, target = await _get_target(ctx, session_id)

    def _write() -> None:
        options = _options_mapping(target)
        if options is not None and hasattr(options, "__setitem__"):
            options[key] = value
            return
        target.set_option(key, value)

    await asyncio.to_thread(_write)
    return {"status": "ok", "session_id": session_id, "key": key, "value": value}


# US-customary vs SI partition of the FLOW_UNITS tokens. CFS/GPM/MGD are US
# customary; CMS/LPS/MLD are SI. Kept local so the tool reports units even
# when running against an engine build without the ``Solver.flow_units``
# property (it derives the answer from the FLOW_UNITS option string).
_US_FLOW_UNITS = frozenset({"CFS", "GPM", "MGD"})
_SI_FLOW_UNITS = frozenset({"CMS", "LPS", "MLD"})


@model_mcp.tool()
async def get_unit_system(ctx: Context, session_id: str = "default") -> dict:
    """Report the model's flow units and unit system.

    Because the engine returns every quantity in the units declared in the
    ``.inp`` file (project units), a client must know those units to
    interpret returned magnitudes. This tool resolves ``[OPTIONS]
    FLOW_UNITS`` and classifies it:

    * ``flow_units`` — the raw token, e.g. ``"CFS"`` / ``"CMS"``.
    * ``unit_system`` — ``"US"`` (CFS/GPM/MGD) or ``"SI"`` (CMS/LPS/MLD).

    Works in BUILDING (ModelBuilder) and OPENED/RUNNING/ENDED (Solver)
    states.
    """
    _, target = await _get_target(ctx, session_id)

    def _read() -> str:
        # Prefer the engine's typed accessor (swmm_get_flow_units) when the
        # target is a Solver that exposes it; fall back to the option string.
        flow_units = getattr(target, "flow_units", None)
        if flow_units is not None:
            name = getattr(flow_units, "name", None)
            return name if name is not None else str(flow_units)
        options = _options_mapping(target)
        if options is not None:
            return options["FLOW_UNITS"]
        return target.get_option("FLOW_UNITS")

    raw = await asyncio.to_thread(_read)
    token = raw.strip().upper()
    if token in _US_FLOW_UNITS:
        system = "US"
    elif token in _SI_FLOW_UNITS:
        system = "SI"
    else:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Unrecognised FLOW_UNITS token {raw!r}.")
    return {
        "session_id": session_id,
        "flow_units": token,
        "unit_system": system,
    }


async def _list_named_collection(ctx, session_id, attr, label):
    """Return ``{count, ids}`` for a name-keyed Solver collection."""
    _, target = await _get_target(ctx, session_id)
    coll = getattr(target, attr, None)
    if coll is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] {label} are not available in this session state."
        )
    ids = await asyncio.to_thread(lambda: list(coll))
    return {"session_id": session_id, "count": len(ids), "ids": ids}


@model_mcp.tool()
async def list_aquifers(ctx: Context, session_id: str = "default") -> dict:
    """List the model's ``[AQUIFERS]`` entries.

    Returns ``count`` and the ordered list of aquifer ``ids``.
    """
    return await _list_named_collection(ctx, session_id, "aquifers", "Aquifers")


@model_mcp.tool()
async def list_snowpacks(ctx: Context, session_id: str = "default") -> dict:
    """List the model's ``[SNOWPACKS]`` entries.

    Returns ``count`` and the ordered list of snowpack ``ids``.
    """
    return await _list_named_collection(ctx, session_id, "snowpacks", "Snowpacks")


@model_mcp.tool()
async def get_pattern_factors(
    ctx: Context, session_id: str = "default", pattern_id: str = ""
) -> dict:
    """Read a time pattern's type and multiplier factors.

    Surfaces ``solver.patterns[...]`` — the multiplier list whose length
    depends on the pattern type (12 monthly, 7 daily, 24 hourly/weekend).

    Parameters
    ----------
    pattern_id:
        The ``[PATTERNS]`` id to read (required).
    """
    if not pattern_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] pattern_id must not be empty.")
    _, target = await _get_target(ctx, session_id)
    patterns = getattr(target, "patterns", None)
    if patterns is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Patterns are not available in this session state."
        )

    def _read():
        pat = patterns[pattern_id]
        return {"type": pat.type.name, "factors": list(pat.factors)}

    try:
        data = await asyncio.to_thread(_read)
    except (KeyError, IndexError) as exc:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] No pattern with id {pattern_id!r}."
        ) from exc
    return {"session_id": session_id, "pattern_id": pattern_id, **data}


@model_mcp.tool()
async def get_option_ext(ctx: Context, session_id: str = "default", key: str = "") -> dict:
    """Return an extension option value (unknown to base SWMM)."""
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] key must not be empty.")
    _, target = await _get_target(ctx, session_id)

    def _read() -> str:
        options = _options_mapping(target)
        if options is not None:
            # v1 Solver: extension options live on options.ext.
            ext = getattr(options, "ext", None)
            if ext is not None:
                return ext[key]
        return target.get_option_ext(key)

    value = await asyncio.to_thread(_read)
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

    def _write() -> None:
        options = _options_mapping(target)
        if options is not None:
            ext = getattr(options, "ext", None)
            if ext is not None and hasattr(ext, "__setitem__"):
                ext[key] = value
                return
        target.set_option_ext(key, value)

    await asyncio.to_thread(_write)
    return {"status": "ok", "session_id": session_id, "key": key, "value": value}


@model_mcp.tool()
async def get_crs(ctx: Context, session_id: str = "default") -> dict:
    """Return the model's coordinate reference system string."""
    _, target = await _get_target(ctx, session_id)

    def _read() -> str:
        # Solver: ``crs`` is a property; ModelBuilder: ``get_crs()`` method.
        if not hasattr(target, "get_crs"):
            return target.crs
        crs_attr = getattr(target, "crs", None)
        if crs_attr is not None and not callable(crs_attr):
            return crs_attr
        return target.get_crs()

    crs = await asyncio.to_thread(_read)
    return {"session_id": session_id, "crs": crs}


# ===========================================================================
# Report start date/time
# ===========================================================================


@model_mcp.tool()
async def get_report_start(ctx: Context, session_id: str = "default") -> dict:
    """Return the report start date/time as an ISO 8601 string.

    Surfaces the ``report_start_datetime`` property (present on both
    ModelBuilder and Solver). The report start is the instant from which
    reported results begin; it may lag the simulation start.
    """
    _, target = await _get_target(ctx, session_id)
    dt = await asyncio.to_thread(lambda: target.report_start_datetime)
    return {"session_id": session_id, "report_start": dt.isoformat()}


@model_mcp.tool()
async def set_report_start(
    ctx: Context,
    session_id: str = "default",
    report_start: str = "",
) -> dict:
    """Set the report start date/time from an ISO 8601 string.

    ``report_start`` is parsed with :meth:`datetime.datetime.fromisoformat`
    (e.g. ``"1998-01-01T00:00:00"`` or ``"1998-01-01 00:00:00"``).
    """
    if not report_start:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] report_start must not be empty.")
    try:
        dt = datetime.fromisoformat(report_start)
    except ValueError as exc:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] report_start {report_start!r} is not a "
            f"valid ISO 8601 date/time."
        ) from exc
    _, target = await _get_target(ctx, session_id)

    def _write() -> None:
        target.report_start_datetime = dt

    await asyncio.to_thread(_write)
    return {"status": "ok", "session_id": session_id, "report_start": dt.isoformat()}


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
# User-flag schema definitions ([USER_FLAGS]) + per-object values
# ([USER_FLAG_VALUES])
# ===========================================================================

# Flag type tokens <-> openswmm.engine.UserFlagType codes.
_USERFLAG_TYPES: dict[str, int] = {"BOOLEAN": 0, "INTEGER": 1, "REAL": 2, "STRING": 3}
_USERFLAG_TYPE_NAMES: dict[int, str] = {v: k for k, v in _USERFLAG_TYPES.items()}


class _UserFlagSchemaOps:
    """Uniform schema/value operations over either engine surface.

    ``ModelBuilder`` exposes ``define_userflag`` etc. directly; ``Solver``
    exposes the same operations on the ``solver.userflags`` view.
    """

    def __init__(self, target):
        if hasattr(target, "define_userflag"):  # ModelBuilder
            self.define = target.define_userflag
            self.undefine = target.undefine_userflag
            self.def_count = target.userflag_def_count
            self.def_get = target.get_userflag_def
            self.value_get = target.get_userflag_value
            self.value_set = target.set_userflag_value
            self.value_clear = target.clear_userflag_value
        else:  # Solver -> UserFlags view
            flags = target.userflags
            self.define = flags.define
            self.undefine = flags.undefine
            self.def_count = lambda: len(flags.definitions())
            self.def_get = lambda i: tuple(flags.definitions()[i])
            self.value_get = flags.get_value
            self.value_set = flags.set_value
            self.value_clear = flags.clear_value


def _resolve_userflag_type(flag_type: str) -> int:
    token = flag_type.strip().upper()
    if token not in _USERFLAG_TYPES:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid flag type '{flag_type}'. "
            f"Must be one of: {', '.join(_USERFLAG_TYPES)}"
        )
    return _USERFLAG_TYPES[token]


@model_mcp.tool()
async def userflag_define(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    flag_type: str = "",
    description: str = "",
) -> dict:
    """Define (or redefine) a user-flag schema entry ([USER_FLAGS]).

    ``flag_type`` is ``"BOOLEAN"``, ``"INTEGER"``, ``"REAL"``, or
    ``"STRING"``. The name is stored uppercase. Redefining an existing
    name overwrites its definition; previously assigned per-object values
    are kept as-is.
    """
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] name must not be empty.")
    type_code = _resolve_userflag_type(flag_type)
    _, target = await _get_target(ctx, session_id)
    ops = _UserFlagSchemaOps(target)
    await asyncio.to_thread(ops.define, name, type_code, description)
    return {
        "status": "ok",
        "session_id": session_id,
        "name": name.upper(),
        "flag_type": _USERFLAG_TYPE_NAMES[type_code],
        "description": description,
    }


@model_mcp.tool()
async def userflag_undefine(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
) -> dict:
    """Remove a user-flag definition and all per-object values assigned to it."""
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] name must not be empty.")
    _, target = await _get_target(ctx, session_id)
    ops = _UserFlagSchemaOps(target)
    await asyncio.to_thread(ops.undefine, name)
    return {"status": "ok", "session_id": session_id, "removed": name.upper()}


@model_mcp.tool()
async def userflag_list_defs(ctx: Context, session_id: str = "default") -> dict:
    """List every user-flag schema definition ([USER_FLAGS]), in insertion order.

    Each entry reports ``name``, ``flag_type`` (BOOLEAN / INTEGER / REAL /
    STRING), and ``description``.
    """
    _, target = await _get_target(ctx, session_id)
    ops = _UserFlagSchemaOps(target)

    def _read() -> list[dict]:
        out = []
        for i in range(ops.def_count()):
            name, type_code, desc = ops.def_get(i)
            out.append(
                {
                    "name": name,
                    "flag_type": _USERFLAG_TYPE_NAMES.get(int(type_code), str(type_code)),
                    "description": desc,
                }
            )
        return out

    defs = await asyncio.to_thread(_read)
    return {"session_id": session_id, "count": len(defs), "definitions": defs}


@model_mcp.tool()
async def userflag_get_value(
    ctx: Context,
    session_id: str = "default",
    obj_type: str = "",
    obj_name: str = "",
    flag_name: str = "",
) -> dict:
    """Return the flag value assigned to a specific object ([USER_FLAG_VALUES]).

    ``obj_type`` is an object type token (e.g. ``"NODE"``, ``"LINK"``,
    ``"SUBCATCHMENT"``). The value is returned in its INP string form
    (BOOLEAN as YES/NO, INTEGER/REAL as decimals, STRING verbatim);
    ``value`` is ``None`` and ``assigned`` is ``False`` when unset.
    """
    if not obj_type or not obj_name or not flag_name:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] obj_type, obj_name, and flag_name must not be empty."
        )
    _, target = await _get_target(ctx, session_id)
    ops = _UserFlagSchemaOps(target)
    value = await asyncio.to_thread(ops.value_get, obj_type, obj_name, flag_name)
    return {
        "session_id": session_id,
        "obj_type": obj_type.upper(),
        "obj_name": obj_name,
        "flag_name": flag_name.upper(),
        "assigned": value is not None,
        "value": value,
    }


@model_mcp.tool()
async def userflag_set_value(
    ctx: Context,
    session_id: str = "default",
    obj_type: str = "",
    obj_name: str = "",
    flag_name: str = "",
    value: str = "",
) -> dict:
    """Assign a flag value to a specific object from a string.

    The flag must already be defined (see ``model_userflag_define``); its
    declared type drives parsing. BOOLEAN accepts YES/NO/TRUE/FALSE/1/0;
    INTEGER a decimal integer; REAL a decimal number; STRING is stored
    verbatim.
    """
    if not obj_type or not obj_name or not flag_name:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] obj_type, obj_name, and flag_name must not be empty."
        )
    _, target = await _get_target(ctx, session_id)
    ops = _UserFlagSchemaOps(target)
    await asyncio.to_thread(ops.value_set, obj_type, obj_name, flag_name, value)
    return {
        "status": "ok",
        "session_id": session_id,
        "obj_type": obj_type.upper(),
        "obj_name": obj_name,
        "flag_name": flag_name.upper(),
        "value": value,
    }


@model_mcp.tool()
async def userflag_clear_value(
    ctx: Context,
    session_id: str = "default",
    obj_type: str = "",
    obj_name: str = "",
    flag_name: str = "",
) -> dict:
    """Remove the flag value assigned to a specific object (idempotent)."""
    if not obj_type or not obj_name or not flag_name:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] obj_type, obj_name, and flag_name must not be empty."
        )
    _, target = await _get_target(ctx, session_id)
    ops = _UserFlagSchemaOps(target)
    await asyncio.to_thread(ops.value_clear, obj_type, obj_name, flag_name)
    return {
        "status": "ok",
        "session_id": session_id,
        "obj_type": obj_type.upper(),
        "obj_name": obj_name,
        "flag_name": flag_name.upper(),
    }


# ===========================================================================
# External-file path slots (typed; reaches every slot, not just [FILES])
# ===========================================================================

# Role tokens <-> openswmm.engine.FilePathRole codes (SWMM_FilePathRole).
_FILE_PATH_ROLES: dict[str, int] = {
    "RAINFALL": 1,
    "RUNOFF": 2,
    "RDII": 3,
    "INFLOWS": 4,
    "OUTFLOWS": 5,
    "HOTSTART_USE": 6,
    "CLIMATE_TEMP": 7,
    "HOTSTART_SAVE": 8,
    "RAINGAGE_DATA": 9,
    "TIMESERIES_DATA": 10,
}
_VECTOR_FILE_PATH_ROLES = frozenset({"HOTSTART_SAVE", "RAINGAGE_DATA", "TIMESERIES_DATA"})


def _resolve_file_path_role(role: str) -> tuple[str, int]:
    token = role.strip().upper()
    if token not in _FILE_PATH_ROLES:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid file-path role '{role}'. "
            f"Must be one of: {', '.join(_FILE_PATH_ROLES)}"
        )
    return token, _FILE_PATH_ROLES[token]


@model_mcp.tool()
async def file_path_get(
    ctx: Context,
    session_id: str = "default",
    role: str = "",
    owner: str = "",
) -> dict:
    """Read an external-file slot's resolved and original paths.

    ``role`` selects the slot: scalar roles ``RAINFALL``, ``RUNOFF``,
    ``RDII``, ``INFLOWS``, ``OUTFLOWS``, ``HOTSTART_USE``, ``CLIMATE_TEMP``
    (``owner`` ignored), or vector roles ``HOTSTART_SAVE`` (owner = decimal
    index), ``RAINGAGE_DATA`` (owner = gage id), ``TIMESERIES_DATA``
    (owner = series id). Returns both the engine-resolved absolute path and
    the original token as authored in the ``.inp``; either may be empty.
    """
    token, code = _resolve_file_path_role(role)
    if token in _VECTOR_FILE_PATH_ROLES and not owner:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Role '{token}' is a vector slot "
            f"and requires an owner key."
        )
    _, target = await _get_builder(ctx, session_id)
    absolute, original = await asyncio.to_thread(target.get_file_path, code, owner)
    return {
        "session_id": session_id,
        "role": token,
        "owner": owner,
        "absolute": absolute,
        "original": original,
    }


@model_mcp.tool()
async def file_path_set(
    ctx: Context,
    session_id: str = "default",
    role: str = "",
    new_path: str = "",
    owner: str = "",
) -> dict:
    """Set the original token for an external-file slot.

    Clears the cached absolute resolution (the engine re-resolves on next
    use). For vector roles the ``owner`` must already exist in the model.
    Pass an empty ``new_path`` to clear the slot. See ``model_file_path_get``
    for the role list.
    """
    token, code = _resolve_file_path_role(role)
    if token in _VECTOR_FILE_PATH_ROLES and not owner:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Role '{token}' is a vector slot "
            f"and requires an owner key."
        )
    _, target = await _get_builder(ctx, session_id)
    await asyncio.to_thread(target.set_file_path, code, new_path, owner)
    return {
        "status": "ok",
        "session_id": session_id,
        "role": token,
        "owner": owner,
        "new_path": new_path,
    }


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
