"""Model-editing tools — object deletion and type conversion.

These tools operate on sessions in ``building`` (programmatic construction)
or ``opened`` (after parsing a ``.inp`` file) state.  They expose the
``ModelEditor`` C API surface: non-destructive impact analysis, cascade
deletion, and in-place node / link type conversion.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP
from openswmm.engine import ModelEditor

from openswmm_mcp.dependencies import get_session_manager, require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import ConversionResultModel, ImpactEntryModel, ImpactReportModel
from openswmm_mcp.session import SimSession

logger = logging.getLogger(__name__)

editing_mcp = FastMCP("editing")

# ---------------------------------------------------------------------------
# Type-name → int maps (mirror the C API enum values)
# ---------------------------------------------------------------------------

_NODE_TYPES: dict[str, int] = {
    "junction": 0,
    "outfall": 1,
    "storage": 2,
    "divider": 3,
}

_LINK_TYPES: dict[str, int] = {
    "conduit": 0,
    "pump": 1,
    "orifice": 2,
    "weir": 3,
    "outlet": 4,
}

_NODE_TYPE_NAMES: dict[int, str] = {v: k for k, v in _NODE_TYPES.items()}
_LINK_TYPE_NAMES: dict[int, str] = {v: k for k, v in _LINK_TYPES.items()}

_OBJECT_TYPES = {"node", "link", "subcatchment", "gage", "table", "transect"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_node_type(name: str) -> int:
    key = name.strip().lower()
    if key not in _NODE_TYPES:
        valid = ", ".join(sorted(_NODE_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown node_type '{name}'. Valid: {valid}."
        )
    return _NODE_TYPES[key]


def _resolve_link_type(name: str) -> int:
    key = name.strip().lower()
    if key not in _LINK_TYPES:
        valid = ", ".join(sorted(_LINK_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown link_type '{name}'. Valid: {valid}."
        )
    return _LINK_TYPES[key]


async def _get_editable_session(ctx: Context, session_id: str) -> SimSession:
    """Retrieve a session in 'building' or 'opened' state."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Model editing (deletion / type conversion)")
    if session.state not in ("building", "opened"):
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{session.state}'. Editing tools require 'building' or 'opened' state."
        )
    return session


def _make_editor(session: SimSession) -> ModelEditor:
    """Create a ModelEditor from whichever engine handle is live."""
    if session.state == "building":
        if session.model_builder is None:
            raise ToolError(f"[{ErrorCode.INVALID_STATE}] No ModelBuilder attached to session.")
        return ModelEditor(session.model_builder)
    # "opened" state: solver is available
    return ModelEditor(session.solver)


def _entry_to_model(e) -> ImpactEntryModel:
    return ImpactEntryModel(
        obj_type=e.obj_type,
        obj_type_name=e.obj_type_name,
        obj_idx=e.obj_idx,
        field=e.field,
        cascaded=e.cascaded,
    )


# ---------------------------------------------------------------------------
# Tools — impact analysis (non-destructive)
# ---------------------------------------------------------------------------


@editing_mcp.tool()
async def analyze_impact(
    ctx: Context,
    session_id: str = "default",
    object_type: str = "node",
    object_id: str = "",
) -> ImpactReportModel:
    """Preview what would be affected if an object were deleted, without deleting it.

    Use this to inspect cascades and reference nullifications before calling
    ``delete_object``.  No objects are modified.

    Parameters
    ----------
    object_type:
        One of ``node``, ``link``, ``subcatchment``, ``gage``, ``table``,
        ``transect``.
    object_id:
        The object's string identifier (or a numeric index as a string for
        ``transect``).
    """
    if not object_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] object_id must not be empty.")

    obj_type_key = object_type.strip().lower()
    if obj_type_key not in _OBJECT_TYPES:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown object_type '{object_type}'. "
            f"Valid: {', '.join(sorted(_OBJECT_TYPES))}."
        )

    session = await _get_editable_session(ctx, session_id)
    editor = _make_editor(session)

    try:
        match obj_type_key:
            case "node":
                impacts = await asyncio.to_thread(editor.analyze_node_impact, object_id)
            case "link":
                impacts = await asyncio.to_thread(editor.analyze_link_impact, object_id)
            case "subcatchment":
                impacts = await asyncio.to_thread(editor.analyze_subcatch_impact, object_id)
            case "gage":
                impacts = await asyncio.to_thread(editor.analyze_gage_impact, object_id)
            case "table":
                impacts = await asyncio.to_thread(editor.analyze_table_impact, object_id)
            case "transect":
                impacts = await asyncio.to_thread(editor.analyze_transect_impact, int(object_id))
            case _:
                impacts = []
    except (KeyError, ValueError) as exc:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] {object_type.capitalize()} '{object_id}'"
            f" not found: {exc}"
        )
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    return ImpactReportModel(
        session_id=session_id,
        object_type=obj_type_key,
        object_id=object_id,
        dry_run=True,
        node_count=editor.node_count,
        link_count=editor.link_count,
        impacts=[_entry_to_model(e) for e in impacts],
    )


# ---------------------------------------------------------------------------
# Tools — deletion
# ---------------------------------------------------------------------------


@editing_mcp.tool()
async def delete_object(
    ctx: Context,
    session_id: str = "default",
    object_type: str = "node",
    object_id: str = "",
    dry_run: bool = False,
) -> ImpactReportModel:
    """Delete a model object and cascade-delete or nullify all referencing objects.

    When ``dry_run`` is ``True`` the impact is analysed but nothing is deleted
    (equivalent to :func:`analyze_impact`).

    **Cascade policy**

    * Links that reference a deleted node as an endpoint are **deleted**.
    * Subcatchment ``outlet_node``, inlet-usage ``node_index``, and similar
      weak references are **nullified** (set to -1).
    * All integer cross-references whose value exceeded the deleted index are
      decremented by 1.

    Parameters
    ----------
    object_type:
        One of ``node``, ``link``, ``subcatchment``, ``gage``, ``table``,
        ``transect``.
    object_id:
        String identifier of the object to delete, or a numeric index string
        for ``transect``.
    dry_run:
        When ``True``, return the impact report without mutating the model.
    """
    if not object_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] object_id must not be empty.")

    obj_type_key = object_type.strip().lower()
    if obj_type_key not in _OBJECT_TYPES:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Unknown object_type '{object_type}'.")

    session = await _get_editable_session(ctx, session_id)
    editor = _make_editor(session)

    try:
        if dry_run:
            match obj_type_key:
                case "node":
                    impacts = await asyncio.to_thread(editor.analyze_node_impact, object_id)
                case "link":
                    impacts = await asyncio.to_thread(editor.analyze_link_impact, object_id)
                case "subcatchment":
                    impacts = await asyncio.to_thread(editor.analyze_subcatch_impact, object_id)
                case "gage":
                    impacts = await asyncio.to_thread(editor.analyze_gage_impact, object_id)
                case "table":
                    impacts = await asyncio.to_thread(editor.analyze_table_impact, object_id)
                case "transect":
                    impacts = await asyncio.to_thread(
                        editor.analyze_transect_impact, int(object_id)
                    )
                case _:
                    impacts = []
        else:
            match obj_type_key:
                case "node":
                    impacts = await asyncio.to_thread(editor.delete_node, object_id)
                case "link":
                    impacts = await asyncio.to_thread(editor.delete_link, object_id)
                case "subcatchment":
                    impacts = await asyncio.to_thread(editor.delete_subcatch, object_id)
                case "gage":
                    impacts = await asyncio.to_thread(editor.delete_gage, object_id)
                case "table":
                    impacts = await asyncio.to_thread(editor.delete_table, object_id)
                case "transect":
                    impacts = await asyncio.to_thread(editor.delete_transect, int(object_id))
                case _:
                    impacts = []

    except (KeyError, ValueError) as exc:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] {object_type.capitalize()} '{object_id}'"
            f" not found: {exc}"
        )
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    if not dry_run:
        action = "deleted" if not dry_run else "analysed"
        logger.info(
            "Session '%s': %s '%s' %s (%d impacts).",
            session_id,
            obj_type_key,
            object_id,
            action,
            len(impacts),
        )

    return ImpactReportModel(
        session_id=session_id,
        object_type=obj_type_key,
        object_id=object_id,
        dry_run=dry_run,
        node_count=editor.node_count,
        link_count=editor.link_count,
        impacts=[_entry_to_model(e) for e in impacts],
    )


# ---------------------------------------------------------------------------
# Tools — type conversion
# ---------------------------------------------------------------------------


@editing_mcp.tool()
async def convert_node(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
    new_type: str = "junction",
) -> ConversionResultModel:
    """Convert a node to a different type in place.

    Common properties (invert elevation, max depth, coordinates) are preserved.
    Type-specific properties for the old type are cleared and sensible defaults
    for the new type are applied.  Non-fatal topology warnings are reported but
    do not prevent conversion.

    Parameters
    ----------
    node_id:
        Node identifier or zero-based index.
    new_type:
        Target type: ``junction``, ``outfall``, ``storage``, or ``divider``.
    """
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")

    type_code = _resolve_node_type(new_type)
    session = await _get_editable_session(ctx, session_id)
    editor = _make_editor(session)

    try:
        result = await asyncio.to_thread(editor.convert_node, node_id, type_code)
    except KeyError as exc:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found: {exc}")
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    logger.info("Session '%s': node '%s' converted to %s.", session_id, node_id, new_type)

    return ConversionResultModel(
        session_id=session_id,
        object_type="node",
        object_id=str(node_id),
        new_type=_NODE_TYPE_NAMES.get(result.new_type, str(result.new_type)),
        cleared_fields=result.cleared_fields,
        warnings=result.warnings,
    )


@editing_mcp.tool()
async def convert_link(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    new_type: str = "conduit",
) -> ConversionResultModel:
    """Convert a link to a different type in place.

    Common properties (endpoint nodes, offsets, initial flow) are preserved.
    Type-specific properties are cleared and new-type defaults applied.

    Parameters
    ----------
    link_id:
        Link identifier or zero-based index.
    new_type:
        Target type: ``conduit``, ``pump``, ``orifice``, ``weir``, or ``outlet``.
    """
    if not link_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] link_id must not be empty.")

    type_code = _resolve_link_type(new_type)
    session = await _get_editable_session(ctx, session_id)
    editor = _make_editor(session)

    try:
        result = await asyncio.to_thread(editor.convert_link, link_id, type_code)
    except KeyError as exc:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found: {exc}")
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    logger.info("Session '%s': link '%s' converted to %s.", session_id, link_id, new_type)

    return ConversionResultModel(
        session_id=session_id,
        object_type="link",
        object_id=str(link_id),
        new_type=_LINK_TYPE_NAMES.get(result.new_type, str(result.new_type)),
        cleared_fields=result.cleared_fields,
        warnings=result.warnings,
    )
