"""Controls tools: lifecycle-spanning control-rule management.

Wraps :class:`openswmm.engine.Controls` for full-lifecycle access to the
``[CONTROLS]`` section. Complements (does *not* replace) the two
runtime-only tools in :mod:`openswmm_mcp.tools.forcing`:

* ``forcing.set_link_control`` — single-step pump/orifice/weir setting,
  ``state=running`` only.
* ``forcing.add_control_rule`` — add a rule mid-simulation,
  ``state=running`` only.

The tools here cover the same C API surface but at design / setup time:
read existing rules, list / inspect / clear them, and configure link
behaviour outside of an active simulation step.

C-API state contract (per ``openswmm_controls.h``):

* ``swmm_control_add_rule``, ``swmm_control_count``,
  ``swmm_control_get_rule``, ``swmm_control_clear_rules`` carry no state
  annotation and work in any non-closed state (building, opened,
  initialized, running, ended).
* ``swmm_control_set_link_setting`` and ``swmm_control_set_link_status``
  are documented as ``RUNNING`` state only.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP
from openswmm.engine import BadParamError, Controls

from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
from openswmm_mcp.session import SimSession

controls_mcp = FastMCP("controls")


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Controls (rule management)")
    return session


async def _get_controls_accessor(ctx: Context, session_id: str) -> tuple[SimSession, Any, Any]:
    """Return ``(session, controls, links)`` for any non-closed state.

    For ``building`` sessions, ``controls`` is constructed against the
    attached :class:`ModelBuilder` (along with a :class:`Links` accessor
    for ID resolution). For other states the cached backend accessors are
    returned. ``links`` is included so callers can resolve user-supplied
    string link IDs even when the session is in the ``building`` state.
    """
    session = await _get_session(ctx, session_id)
    state = session.state
    if state == "closed":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is closed; "
            f"open or initialize it before calling Controls tools."
        )

    if state == "building":
        builder = session.model_builder
        if builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in "
                f"'building' state but has no ModelBuilder attached."
            )
        from openswmm.engine import Links

        return session, Controls(builder), Links(builder)

    return session, session.controls, session.links


async def _resolve_link_idx(links: Any, link_id: str | int) -> int:
    """Translate a string link ID to integer index; pass through ints."""
    if isinstance(link_id, int):
        return link_id
    if not link_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] link_id must not be empty.")
    idx = await resolve_index(links, link_id, "Link")
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found in this session."
        )
    return idx


# ===========================================================================
# Rule management (read + lifecycle-spanning mutation)
# ===========================================================================


@controls_mcp.tool()
async def count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of control rules defined in the model."""
    _, controls, _ = await _get_controls_accessor(ctx, session_id)
    n = await asyncio.to_thread(lambda: len(controls))
    return {"session_id": session_id, "count": n}


@controls_mcp.tool()
async def get_rule(ctx: Context, session_id: str = "default", rule_index: int = 0) -> dict:
    """Return the full text of the I{rule_index}-th control rule.

    The rule text is multi-line: a ``RULE <id>`` header followed by ``IF``
    / ``AND`` / ``OR`` clauses and a ``THEN`` action block.
    """
    _, controls, _ = await _get_controls_accessor(ctx, session_id)
    # v1: Controls[idx] returns a ControlRule NamedTuple with .id / .text.
    text = await asyncio.to_thread(lambda: controls[rule_index].text)
    return {
        "session_id": session_id,
        "rule_index": rule_index,
        "text": text,
    }


@controls_mcp.tool()
async def get_id(ctx: Context, session_id: str = "default", rule_index: int = 0) -> dict:
    """Return the canonical rule name parsed from the I{rule_index}-th
    control rule's text (the first token after the ``RULE`` keyword,
    case-insensitive).

    When the rule text is malformed (no parseable ``RULE`` keyword
    token), ``name`` is ``None`` so callers can render a sentinel
    display label like ``Rule N [unnamed]`` without catching exceptions.
    """
    _, controls, _ = await _get_controls_accessor(ctx, session_id)
    def _read_id():
        try:
            return controls[rule_index].id or None
        except ValueError:
            return None

    name = await asyncio.to_thread(_read_id)
    return {
        "session_id": session_id,
        "rule_index": rule_index,
        "name": name,
    }


@controls_mcp.tool()
async def list_rules(ctx: Context, session_id: str = "default") -> dict:
    """Return all control rules as a list of ``{index, name, text}`` dicts."""
    _, controls, _ = await _get_controls_accessor(ctx, session_id)

    def _read_all() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(len(controls)):
            # A nameless rule cannot be materialised: ``controls[i]`` calls
            # ``swmm_control_get_id`` which returns BADPARAM when the rule has
            # no ``RULE <name>`` header. Surface it as a name=None placeholder
            # instead of letting the whole listing fail.
            try:
                rule = controls[i]
                name = rule.id or None
                text = rule.text
            except (ValueError, BadParamError):
                name, text = None, None
            out.append({"index": i, "name": name, "text": text})
        return out

    rules = await asyncio.to_thread(_read_all)
    return {"session_id": session_id, "count": len(rules), "rules": rules}


@controls_mcp.tool()
async def add_rule(ctx: Context, session_id: str = "default", rule_text: str = "") -> dict:
    """Add a control rule to the model (lifecycle-spanning).

    Accepts the full SWMM rule text including the ``RULE <id>`` header,
    one or more ``IF`` / ``AND`` / ``OR`` clauses, and a ``THEN`` action
    block (and optional ``ELSE`` / ``PRIORITY`` clauses). Lines are
    newline-separated within the string.

    Works in any non-closed state. The runtime-only counterpart
    ``forcing.add_control_rule`` enforces ``state=running`` and is the
    right tool when adding rules mid-simulation.

    Example
    -------
    .. code-block:: text

        RULE PUMP_ON
        IF NODE J1 DEPTH > 5.0
        THEN PUMP P1 STATUS = ON
    """
    if not rule_text.strip():
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] rule_text must not be empty.")
    _, controls, _ = await _get_controls_accessor(ctx, session_id)

    def _append() -> int:
        # v1: Controls.append accepts a rule text string, ControlRule, or
        # dict with a 'text' key.  Returns nothing; new index is len()-1.
        controls.append(rule_text)
        return len(controls)

    new_count = await asyncio.to_thread(_append)
    return {
        "status": "ok",
        "session_id": session_id,
        "rule_index": new_count - 1,
        "total_rules": new_count,
    }


@controls_mcp.tool()
async def validate_rule(ctx: Context, session_id: str = "default", rule_text: str = "") -> dict:
    """Validate control-rule text WITHOUT adding it to the model.

    Parses *rule_text* through the engine's rule compiler and reports
    whether it is syntactically valid. On failure ``message`` carries the
    engine's diagnostic string; on success it is empty. Use this to
    pre-flight a rule before committing it via :func:`add_rule` (or the
    runtime ``forcing.add_control_rule``).

    Accepts the full SWMM rule text (the ``RULE <id>`` header, ``IF`` /
    ``AND`` / ``OR`` clauses, and a ``THEN`` action block). Works in any
    non-closed state and never mutates the model.
    """
    if not rule_text.strip():
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] rule_text must not be empty.")
    _, controls, _ = await _get_controls_accessor(ctx, session_id)

    # v1: Controls.validate_message(rule_text) -> (valid: bool, message: str).
    valid, message = await asyncio.to_thread(controls.validate_message, rule_text)
    return {
        "session_id": session_id,
        "valid": bool(valid),
        "message": message,
    }


@controls_mcp.tool()
async def clear_rules(ctx: Context, session_id: str = "default") -> dict:
    """Remove every control rule from the model."""
    _, controls, _ = await _get_controls_accessor(ctx, session_id)
    await asyncio.to_thread(lambda: controls.clear())
    return {"status": "ok", "session_id": session_id, "remaining": 0}


@controls_mcp.tool()
async def remove_rule(ctx: Context, session_id: str = "default", rule_index: int = 0) -> dict:
    """Remove a single control rule by index (later rules shift down by one).

    Wraps ``Controls.remove_rule``. Unlike :func:`clear_rules`, which drops
    every rule, this deletes only the I{rule_index}-th rule; all rules after
    it renumber down. Requires the engine to be in ``building`` or ``opened``
    state.
    """
    _, controls, _ = await _get_controls_accessor(ctx, session_id)

    def _remove() -> int:
        controls.remove_rule(rule_index)
        return len(controls)

    try:
        remaining = await asyncio.to_thread(_remove)
    except IndexError:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] rule_index {rule_index} is out of range."
        )
    return {
        "status": "ok",
        "session_id": session_id,
        "rule_index": rule_index,
        "remaining": remaining,
    }


@controls_mcp.tool()
async def find_references(
    ctx: Context, session_id: str = "default", object_name: str = ""
) -> dict:
    """Return the indices of control rules that reference an object by name.

    Wraps ``Controls.find_references``. Scans each rule's clauses for an
    object-type keyword (``NODE`` / ``LINK`` / ``CONDUIT`` / ``PUMP`` /
    ``ORIFICE`` / ``WEIR`` / ``OUTLET``) immediately followed by
    *object_name* (case-insensitive). Read-only — no rule text is edited.
    Use this before deleting or renaming an object to find the rules that
    would be affected.
    """
    if not object_name.strip():
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] object_name must not be empty.")
    _, controls, _ = await _get_controls_accessor(ctx, session_id)
    indices = await asyncio.to_thread(controls.find_references, object_name)
    rule_indices = [int(i) for i in indices]
    return {
        "session_id": session_id,
        "object_name": object_name,
        "rule_indices": rule_indices,
        "count": len(rule_indices),
    }


# ===========================================================================
# Direct control actions (RUNNING state only)
# ===========================================================================


@controls_mcp.tool()
async def set_link_setting(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    setting: float = 0.0,
) -> dict:
    """Set a continuous control setting on a link (RUNNING state only).

    Maps to ``swmm_control_set_link_setting``. Used for pump speeds,
    orifice openings, weir crest positions — anywhere the engine model
    accepts a 0..1 (or higher, depending on link type) continuous value.

    Distinct from :func:`set_link_status` which sets a discrete OPEN/CLOSED
    state. The plan documents the split: ``setting`` is continuous,
    ``status`` is binary.

    For mid-simulation control, prefer ``forcing.set_link_control`` which
    is functionally equivalent — both wrap the same C call. This tool
    exists in the ``controls`` namespace for naming symmetry with the rest
    of the rule-management surface.
    """
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    link_idx = await _resolve_link_idx(session.links, link_id)
    controls = session.controls
    value = float(setting)
    # v1 set_link_setting takes (link_key, setting) — accepts int or str.
    await asyncio.to_thread(controls.set_link_setting, link_idx, value)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": link_idx,
        "setting": setting,
    }


@controls_mcp.tool()
async def set_link_status(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    open: bool = True,
) -> dict:
    """Set the discrete OPEN/CLOSED status of a link (RUNNING state only).

    Maps to ``swmm_control_set_link_status``. The boolean ``open`` is
    forwarded as the inverse to v1's keyword-only ``closed`` argument.

    For continuous control settings (pump speed, orifice opening),
    use :func:`set_link_setting`.
    """
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    link_idx = await _resolve_link_idx(session.links, link_id)
    controls = session.controls
    closed = not bool(open)
    # v1: set_link_status(link, *, closed=bool) — note keyword-only arg.
    await asyncio.to_thread(lambda: controls.set_link_status(link_idx, closed=closed))
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": link_idx,
        "open": open,
    }
