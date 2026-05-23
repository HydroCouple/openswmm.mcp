"""Model-editing tools — object deletion, type conversion, and property updates.

These tools operate on sessions in ``building`` (programmatic construction)
or ``opened`` (after parsing a ``.inp`` file) state.  They expose the
``ModelEditor`` C API surface: non-destructive impact analysis, cascade
deletion, in-place node / link type conversion, and in-place property updates.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP
from openswmm.engine import ModelEditor

from openswmm_mcp.dependencies import get_session_manager, require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import (
    ConversionResultModel,
    GageConfigResult,
    ImpactEntryModel,
    ImpactReportModel,
    PropertyUpdateResult,
)
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


# ---------------------------------------------------------------------------
# Tools — in-place property updates
# ---------------------------------------------------------------------------

_XSECT_SHAPES: dict[str, int] = {
    "circular": 0,
    "rect_closed": 1,
    "rect_open": 2,
    "trapezoidal": 3,
    "triangular": 4,
    "parabolic": 5,
    "powerfunc": 6,
    "rect_triang": 7,
    "rect_round": 8,
    "modbaskethandle": 9,
    "egg": 10,
    "horseshoe": 11,
    "gothic": 12,
    "catenary": 13,
    "semielliptical": 14,
    "baskethandle": 15,
    "semicircular": 16,
    "irregular": 17,
    "custom": 18,
    "force_main": 19,
    "filled_circular": 20,
}

_GAGE_RAIN_TYPES: dict[str, int] = {
    "intensity": 0,
    "volume": 1,
    "cumulative": 2,
}

_GAGE_DATA_SOURCES: dict[str, int] = {
    "timeseries": 0,
    "file": 1,
}


async def _require_editable(ctx: Context, session_id: str) -> SimSession:
    """Return a session that allows property edits (building, opened, or initialized)."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Property updates")
    if session.state not in ("building", "opened", "initialized"):
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{session.state}'. Property update tools require 'building', "
            f"'opened', or 'initialized' state."
        )
    return session


@editing_mcp.tool()
async def set_node_properties(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
    invert_elev: float | None = None,
    max_depth: float | None = None,
    initial_depth: float | None = None,
    surcharge_depth: float | None = None,
    ponded_area: float | None = None,
) -> PropertyUpdateResult:
    """Update geometry properties of an existing node in place.

    Only fields that are explicitly provided (non-null) are updated.
    All others are left unchanged.  Valid in ``building``, ``opened``,
    or ``initialized`` state.

    Parameters
    ----------
    node_id:
        Node identifier or zero-based index string.
    invert_elev:
        Node invert elevation (project length units).
    max_depth:
        Maximum node depth (project length units).
    initial_depth:
        Initial water depth at simulation start.
    surcharge_depth:
        Surcharge depth above the crown.
    ponded_area:
        Ponded surface area when node is flooded.
    """
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")

    session = await _require_editable(ctx, session_id)
    nodes = session.nodes

    idx = await asyncio.to_thread(nodes.get_index, node_id)
    if idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found.")

    updated: dict[str, float] = {}
    try:
        if invert_elev is not None:
            await asyncio.to_thread(nodes.set_invert_elev, idx, invert_elev)
            updated["invert_elev"] = invert_elev
        if max_depth is not None:
            await asyncio.to_thread(nodes.set_max_depth, idx, max_depth)
            updated["max_depth"] = max_depth
        if initial_depth is not None:
            await asyncio.to_thread(nodes.set_initial_depth, idx, initial_depth)
            updated["initial_depth"] = initial_depth
        if surcharge_depth is not None:
            await asyncio.to_thread(nodes.set_surcharge_depth, idx, surcharge_depth)
            updated["surcharge_depth"] = surcharge_depth
        if ponded_area is not None:
            await asyncio.to_thread(nodes.set_pond_area, idx, ponded_area)
            updated["ponded_area"] = ponded_area
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    logger.info("Session '%s': node '%s' properties updated: %s.", session_id, node_id, updated)
    return PropertyUpdateResult(
        session_id=session_id,
        element_type="node",
        element_id=node_id,
        updated_fields=updated,
    )


@editing_mcp.tool()
async def set_link_properties(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    length: float | None = None,
    roughness: float | None = None,
    offset_up: float | None = None,
    offset_dn: float | None = None,
    initial_flow: float | None = None,
    max_flow: float | None = None,
    xsect_shape: str | None = None,
    xsect_geom1: float | None = None,
    xsect_geom2: float | None = None,
    xsect_geom3: float | None = None,
    xsect_geom4: float | None = None,
) -> PropertyUpdateResult:
    """Update geometry properties of an existing link in place.

    Only fields that are explicitly provided (non-null) are updated.
    Valid in ``building``, ``opened``, or ``initialized`` state.

    Cross-section fields (``xsect_shape``, ``xsect_geom1``–``xsect_geom4``)
    are applied as a group only when ``xsect_shape`` is provided.

    Parameters
    ----------
    link_id:
        Link identifier or zero-based index string.
    length:
        Conduit length (project length units).
    roughness:
        Manning's roughness coefficient.
    offset_up:
        Upstream invert offset above connecting node invert.
    offset_dn:
        Downstream invert offset above connecting node invert.
    initial_flow:
        Initial flow rate at simulation start.
    max_flow:
        Maximum allowable flow rate (0 = no limit).
    xsect_shape:
        Cross-section shape name (e.g. ``circular``, ``rect_closed``).
    xsect_geom1–4:
        Shape geometry parameters (meaning depends on shape type).
    """
    if not link_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] link_id must not be empty.")

    session = await _require_editable(ctx, session_id)
    links = session.links

    idx = await asyncio.to_thread(links.get_index, link_id)
    if idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found.")

    updated: dict[str, float | int | str] = {}
    try:
        if length is not None:
            await asyncio.to_thread(links.set_length, idx, length)
            updated["length"] = length
        if roughness is not None:
            await asyncio.to_thread(links.set_roughness, idx, roughness)
            updated["roughness"] = roughness
        if offset_up is not None:
            await asyncio.to_thread(links.set_offset_up, idx, offset_up)
            updated["offset_up"] = offset_up
        if offset_dn is not None:
            await asyncio.to_thread(links.set_offset_dn, idx, offset_dn)
            updated["offset_dn"] = offset_dn
        if initial_flow is not None:
            await asyncio.to_thread(links.set_initial_flow, idx, initial_flow)
            updated["initial_flow"] = initial_flow
        if max_flow is not None:
            await asyncio.to_thread(links.set_max_flow, idx, max_flow)
            updated["max_flow"] = max_flow
        if xsect_shape is not None:
            shape_key = xsect_shape.strip().lower()
            if shape_key not in _XSECT_SHAPES:
                valid = ", ".join(sorted(_XSECT_SHAPES))
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Unknown xsect_shape '{xsect_shape}'. "
                    f"Valid: {valid}."
                )
            shape_code = _XSECT_SHAPES[shape_key]
            g1 = xsect_geom1 or 0.0
            g2 = xsect_geom2 or 0.0
            g3 = xsect_geom3 or 0.0
            g4 = xsect_geom4 or 0.0
            await asyncio.to_thread(links.set_xsect, idx, shape_code, g1, g2, g3, g4)
            updated["xsect_shape"] = xsect_shape
            updated["xsect_geom1"] = g1
            updated["xsect_geom2"] = g2
            updated["xsect_geom3"] = g3
            updated["xsect_geom4"] = g4
    except ToolError:
        raise
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    logger.info("Session '%s': link '%s' properties updated: %s.", session_id, link_id, updated)
    return PropertyUpdateResult(
        session_id=session_id,
        element_type="link",
        element_id=link_id,
        updated_fields=updated,
    )


@editing_mcp.tool()
async def set_subcatchment_properties(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str = "",
    area: float | None = None,
    width: float | None = None,
    slope: float | None = None,
    imperv_pct: float | None = None,
    n_imperv: float | None = None,
    n_perv: float | None = None,
    ds_imperv: float | None = None,
    ds_perv: float | None = None,
    outlet_node_id: str | None = None,
    gage_id: str | None = None,
) -> PropertyUpdateResult:
    """Update properties of an existing subcatchment in place.

    Only fields that are explicitly provided (non-null) are updated.
    Valid in ``building``, ``opened``, or ``initialized`` state.

    Parameters
    ----------
    subcatch_id:
        Subcatchment identifier.
    area:
        Total subcatchment area (project area units).
    width:
        Characteristic overland flow width (project length units).
    slope:
        Average surface slope (fraction, e.g. 0.01 for 1%).
    imperv_pct:
        Percent imperviousness (0–100).
    n_imperv:
        Manning's roughness for impervious area.
    n_perv:
        Manning's roughness for pervious area.
    ds_imperv:
        Depression storage depth for impervious area.
    ds_perv:
        Depression storage depth for pervious area.
    outlet_node_id:
        ID of the node that receives runoff from this subcatchment.
    gage_id:
        ID of the rain gage that drives this subcatchment.
    """
    if not subcatch_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] subcatch_id must not be empty.")

    session = await _require_editable(ctx, session_id)
    subcatchments = session.subcatchments

    sc_idx = await asyncio.to_thread(subcatchments.get_index, subcatch_id)
    if sc_idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{subcatch_id}' not found.")

    updated: dict[str, float | int | str] = {}
    try:
        if area is not None:
            await asyncio.to_thread(subcatchments.set_area, sc_idx, area)
            updated["area"] = area
        if width is not None:
            await asyncio.to_thread(subcatchments.set_width, sc_idx, width)
            updated["width"] = width
        if slope is not None:
            await asyncio.to_thread(subcatchments.set_slope, sc_idx, slope)
            updated["slope"] = slope
        if imperv_pct is not None:
            await asyncio.to_thread(subcatchments.set_imperv_pct, sc_idx, imperv_pct)
            updated["imperv_pct"] = imperv_pct
        if n_imperv is not None:
            await asyncio.to_thread(subcatchments.set_n_imperv, sc_idx, n_imperv)
            updated["n_imperv"] = n_imperv
        if n_perv is not None:
            await asyncio.to_thread(subcatchments.set_n_perv, sc_idx, n_perv)
            updated["n_perv"] = n_perv
        if ds_imperv is not None:
            await asyncio.to_thread(subcatchments.set_ds_imperv, sc_idx, ds_imperv)
            updated["ds_imperv"] = ds_imperv
        if ds_perv is not None:
            await asyncio.to_thread(subcatchments.set_ds_perv, sc_idx, ds_perv)
            updated["ds_perv"] = ds_perv
        if outlet_node_id is not None:
            nodes = session.nodes
            node_idx = await asyncio.to_thread(nodes.get_index, outlet_node_id)
            if node_idx < 0:
                raise ToolError(
                    f"[{ErrorCode.ELEMENT_NOT_FOUND}] Outlet node '{outlet_node_id}' not found."
                )
            await asyncio.to_thread(subcatchments.set_outlet, sc_idx, node_idx)
            updated["outlet_node_id"] = outlet_node_id
        if gage_id is not None:
            gages = session.gages
            gage_idx = await asyncio.to_thread(gages.get_index, gage_id)
            if gage_idx < 0:
                raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Gage '{gage_id}' not found.")
            await asyncio.to_thread(subcatchments.set_gage, sc_idx, gage_idx)
            updated["gage_id"] = gage_id
    except ToolError:
        raise
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    logger.info(
        "Session '%s': subcatchment '%s' properties updated: %s.",
        session_id,
        subcatch_id,
        updated,
    )
    return PropertyUpdateResult(
        session_id=session_id,
        element_type="subcatchment",
        element_id=subcatch_id,
        updated_fields=updated,
    )


@editing_mcp.tool()
async def configure_gage(
    ctx: Context,
    session_id: str = "default",
    gage_id: str = "",
    rain_type: str | None = None,
    rain_interval: float | None = None,
    data_source: str | None = None,
    timeseries_id: str | None = None,
    filename: str | None = None,
    station_id: str | None = None,
) -> GageConfigResult:
    """Configure a rain gage's data source and recording parameters.

    Only fields that are explicitly provided (non-null) are updated.
    Valid in ``building``, ``opened``, or ``initialized`` state.

    Parameters
    ----------
    gage_id:
        Gage identifier.
    rain_type:
        Rainfall measurement type: ``intensity``, ``volume``, or ``cumulative``.
    rain_interval:
        Recording interval in seconds.
    data_source:
        Data source type: ``timeseries`` or ``file``.
    timeseries_id:
        ID of the time-series table to use (when data_source is ``timeseries``).
    filename:
        Path to an external rainfall data file (when data_source is ``file``).
    station_id:
        Station identifier within the external file.
    """
    if not gage_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] gage_id must not be empty.")

    session = await _require_editable(ctx, session_id)
    gages = session.gages

    g_idx = await asyncio.to_thread(gages.get_index, gage_id)
    if g_idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Gage '{gage_id}' not found.")

    updated: dict[str, str | float | int] = {}
    try:
        if rain_type is not None:
            key = rain_type.strip().lower()
            if key not in _GAGE_RAIN_TYPES:
                valid = ", ".join(sorted(_GAGE_RAIN_TYPES))
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Unknown rain_type '{rain_type}'. "
                    f"Valid: {valid}."
                )
            await asyncio.to_thread(gages.set_rain_type, g_idx, _GAGE_RAIN_TYPES[key])
            updated["rain_type"] = rain_type
        if rain_interval is not None:
            await asyncio.to_thread(gages.set_rain_interval, g_idx, rain_interval)
            updated["rain_interval"] = rain_interval
        if data_source is not None:
            key = data_source.strip().lower()
            if key not in _GAGE_DATA_SOURCES:
                valid = ", ".join(sorted(_GAGE_DATA_SOURCES))
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Unknown data_source '{data_source}'. "
                    f"Valid: {valid}."
                )
            await asyncio.to_thread(gages.set_data_source, g_idx, _GAGE_DATA_SOURCES[key])
            updated["data_source"] = data_source
        if timeseries_id is not None:
            await asyncio.to_thread(gages.set_timeseries, g_idx, timeseries_id)
            updated["timeseries_id"] = timeseries_id
        if filename is not None:
            sid = station_id or ""
            await asyncio.to_thread(gages.set_filename, g_idx, filename, sid)
            updated["filename"] = filename
            if station_id:
                updated["station_id"] = station_id
    except ToolError:
        raise
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    logger.info("Session '%s': gage '%s' configured: %s.", session_id, gage_id, updated)
    return GageConfigResult(
        session_id=session_id,
        gage_id=gage_id,
        updated_fields=updated,
    )


# ===========================================================================
# Rename tools (Phase 2 wave 2 — close the three honest mcp-gaps)
#
# Each accessor class (Nodes / Links / Subcatchments / Gages) carries a
# .rename(idx, new_id) method that wraps the corresponding C API. These
# tools surface those wrappers with consistent error handling and
# session-state semantics.
# ===========================================================================


async def _resolve_for_rename(session, accessor_name: str, current_id: str) -> tuple[object, int]:
    """Resolve a current id string to (accessor, idx) for the rename helpers."""
    if not current_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] {accessor_name[:-1]}_id must not be empty."
        )
    accessor = getattr(session, accessor_name)
    idx = await asyncio.to_thread(accessor.get_index, current_id)
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] "
            f"{accessor_name[:-1].capitalize()} '{current_id}' not found."
        )
    return accessor, idx


@editing_mcp.tool()
async def rename_node(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
    new_id: str = "",
) -> dict:
    """Rename a node (wraps ``swmm_node_rename``).

    The new id must be unique across the node namespace and non-empty.
    The engine returns SWMM_ERR_BADPARAM on collision or empty input.
    """
    if not new_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] new_id must not be empty.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Node rename")
    accessor, idx = await _resolve_for_rename(session, "nodes", node_id)
    await asyncio.to_thread(accessor.rename, idx, new_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "element_type": "node",
        "old_id": node_id,
        "new_id": new_id,
        "index": idx,
    }


@editing_mcp.tool()
async def rename_link(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    new_id: str = "",
) -> dict:
    """Rename a link (wraps ``swmm_link_rename``)."""
    if not new_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] new_id must not be empty.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Link rename")
    accessor, idx = await _resolve_for_rename(session, "links", link_id)
    await asyncio.to_thread(accessor.rename, idx, new_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "element_type": "link",
        "old_id": link_id,
        "new_id": new_id,
        "index": idx,
    }


@editing_mcp.tool()
async def rename_subcatchment(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str = "",
    new_id: str = "",
) -> dict:
    """Rename a subcatchment (wraps ``swmm_subcatch_rename``)."""
    if not new_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] new_id must not be empty.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Subcatchment rename")
    accessor, idx = await _resolve_for_rename(session, "subcatchments", subcatch_id)
    await asyncio.to_thread(accessor.rename, idx, new_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "element_type": "subcatchment",
        "old_id": subcatch_id,
        "new_id": new_id,
        "index": idx,
    }


@editing_mcp.tool()
async def rename_gage(
    ctx: Context,
    session_id: str = "default",
    gage_id: str = "",
    new_id: str = "",
) -> dict:
    """Rename a rain gage (wraps ``swmm_gage_rename``)."""
    if not new_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] new_id must not be empty.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Gage rename")
    accessor, idx = await _resolve_for_rename(session, "gages", gage_id)
    await asyncio.to_thread(accessor.rename, idx, new_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "element_type": "gage",
        "old_id": gage_id,
        "new_id": new_id,
        "index": idx,
    }
