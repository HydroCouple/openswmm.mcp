# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Caleb Buahin
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Reaction-system tools — multi-species reaction expressions, their vocabulary,
and the whole-file ``.rxn`` text surface.

Wraps the engine's ``Reactions`` view (``session.reactions``, C API
``openswmm_reactions.h``): the ``[REACTION_*]`` sections that declare species,
coefficients and intermediate terms, the per-species pipe and tank rate
expressions, ``[REACTION_OPTIONS]``, and the initial-quality rows the reaction
system seeds itself from.

**Pre-flight expressions instead of submitting blind.** Two tools exist purely
so a caller never has to learn the grammar from a rejection:
``reactions_validate_expression`` compiles ONE expression against the model's
live vocabulary and returns a diagnostic with a 1-based error column, and
``reactions_check_text`` dry-runs a WHOLE ``.rxn`` file. Neither changes any
state on success or on failure. Use them before ``reactions_set_species_expression``
or ``reactions_apply_text``.

**The vocabulary is authoritative, not guessable.** Enumerate what an
expression may reference from ``reactions_list_species``,
``reactions_list_coefficients``, ``reactions_list_terms``,
``reactions_hydraulic_variables`` and ``reactions_functions`` rather than
hard-coding identifier lists — these come from the expression compiler's own
tables, so they cannot drift from what actually compiles. The last two are
ENGINE-LESS STATICS: they take no session and work before any model is open.

**Mutation is transactional and BUILDING/OPENED only.** Every mutator
recompiles the whole system before returning, and a mutation that would leave
any expression uncompilable is ROLLED BACK and refused — the model can never be
left storing something that will not compile. ``reactions_apply_text`` is
staged the same way: on any error the previous system is byte-identical to what
it was before the call. Rows only seed state at initialize(), which is why
edits stop at ``opened``.

Two consequences worth knowing before editing: removing a species, coefficient
or term that any compiled expression still references is refused (drop the
references first), and ``reactions_apply_text`` invalidates every index —
re-enumerate afterwards rather than reusing cached positions.

``reactions_serialize`` and ``reactions_apply_text`` round-trip byte-identically,
so serialize-edit-apply is a safe editing loop. Requires the ``openswmm``
backend.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP

from openswmm_mcp._util.transport_enums import (
    coerce_enum,
    name_for,
    reaction_expr_form_codes,
    reaction_scope_codes,
)
from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

logger = logging.getLogger(__name__)

reactions_mcp = FastMCP("reactions")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_READ_STATES = ("building", "opened", "initialized", "running", "ended")
# Reaction rows only seed state at initialize(), and every mutator recompiles
# the whole system, so mutation is BUILDING/OPENED only.
_EDIT_STATES = ("building", "opened")


async def _readable_reactions(ctx: Context, session_id: str) -> tuple[SimSession, object]:
    """Return ``(session, reactions_view)`` for a read."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Reaction system")
    require_state(session, *_READ_STATES)
    return session, session.reactions


async def _editable_reactions(ctx: Context, session_id: str) -> tuple[SimSession, object]:
    """Return ``(session, reactions_view)`` for an edit (BUILDING/OPENED)."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Reaction system editing")
    require_state(session, *_EDIT_STATES)
    return session, session.reactions


def _apply(fn):
    """Run a blocking reaction closure, mapping engine refusals to ``ToolError``."""
    try:
        return fn()
    except ToolError:
        raise
    except Exception as exc:  # EngineError subclasses + ValueError from the C layer
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}") from exc


def _expr_dict(pair) -> dict:
    """Render a ``(form, expression)`` tuple with both the code and the name."""
    form, expression = pair
    codes = reaction_expr_form_codes()
    return {
        "form_code": int(form),
        "form": name_for(int(form), codes),
        "expression": str(expression),
    }


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------


@reactions_mcp.tool()
async def list_species(ctx: Context, session_id: str = "default") -> dict:
    """List every declared reaction species with its tolerances and expressions.

    Each row carries ``index``, ``name``, ``is_wall`` (false = BULK, a
    concentration in the water column; true = WALL, a surface species),
    ``units``, ``atol`` / ``rtol`` (per-species solver tolerances; 0 means
    "take the global ``[REACTION_OPTIONS]`` value"), ``initial`` (the GLOBAL
    initial concentration — per-element seeds are separate, see
    ``reactions_list_initial_quality``), and the ``pipe`` and ``tank``
    expressions each as ``form`` / ``form_code`` / ``expression``.

    A ``form`` of ``none`` means the species has no expression in that scope.
    This list is part of the authoritative expression vocabulary — species
    names here are what an expression may reference.
    """
    # wraps: swmm_reaction_species_count swmm_reaction_species_get swmm_reaction_expr_get swmm_reaction_init_global_get  # noqa: E501
    _, rxn = await _readable_reactions(ctx, session_id)

    def _read() -> dict:
        rows = []
        for species in rxn.species:
            rows.append(
                {
                    "index": int(species.index),
                    "name": str(species.name),
                    "is_wall": bool(species.is_wall),
                    "units": str(species.units),
                    "atol": float(species.atol),
                    "rtol": float(species.rtol),
                    "initial": float(species.initial),
                    "pipe": _expr_dict(species.pipe_expression),
                    "tank": _expr_dict(species.tank_expression),
                }
            )
        return {"count": len(rows), "species": rows}

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@reactions_mcp.tool()
async def add_species(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    wall: bool = False,
    units: str = "",
    atol: float = 0.0,
    rtol: float = 0.0,
) -> dict:
    """Declare a new reaction species.

    ``wall=False`` declares a BULK species (a concentration in the water
    column); ``wall=True`` a WALL species (a surface species). ``units`` is a
    free-form label carried through to output. ``atol`` and ``rtol`` are
    per-species solver tolerances; leave them at 0 to take the global
    ``[REACTION_OPTIONS]`` values.

    The whole system is recompiled before this returns, and the addition is
    rolled back if anything fails to compile. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_species_add
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a species name.")
    _, rxn = await _editable_reactions(ctx, session_id)

    def _add() -> dict:
        species = rxn.species.add(
            name, wall=bool(wall), units=units, atol=float(atol), rtol=float(rtol)
        )
        return {"index": int(species.index), "name": str(species.name)}

    out = await asyncio.to_thread(_apply, _add)
    out["status"] = "ok"
    out["session_id"] = session_id
    out["is_wall"] = bool(wall)
    out["units"] = units
    out["atol"] = float(atol)
    out["rtol"] = float(rtol)
    return out


@reactions_mcp.tool()
async def remove_species(
    ctx: Context,
    session_id: str = "default",
    species: str | int = "",
) -> dict:
    """Remove a species by name or index.

    REFUSED while any compiled expression still references the species — clear
    or rewrite those expressions first (``reactions_set_species_expression``,
    ``reactions_set_term_expression``). The removal rebuilds the species
    registry in place and is rolled back if the rebuilt system does not
    compile. BUILDING/OPENED only, and later indices shift down.
    """
    # wraps: swmm_reaction_species_remove
    if species == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a species name or index.")
    _, rxn = await _editable_reactions(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: rxn.species.remove(species))
    return {"status": "ok", "session_id": session_id, "species": species}


@reactions_mcp.tool()
async def get_species_expression(
    ctx: Context,
    session_id: str = "default",
    species: str | int = "",
    scope: str | int = "pipe",
) -> dict:
    """Read one species' reaction expression in the pipe or tank scope.

    ``scope`` is ``pipe`` (flowing conduits) or ``tank`` (storage units).
    Returns ``form`` / ``form_code`` and ``expression``; a ``form`` of ``none``
    means the species has no expression in that scope and ``expression`` is
    empty.

    The forms are ``rate`` (a d/dt rate expression), ``equil`` (an equilibrium
    expression driven to zero) and ``formula`` (an explicit algebraic
    definition).
    """
    # wraps: swmm_reaction_expr_get
    if species == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a species name or index.")
    _, rxn = await _readable_reactions(ctx, session_id)
    scope_codes = reaction_scope_codes()
    scope_code = coerce_enum(scope, scope_codes, "reaction scope")

    def _read() -> dict:
        return _expr_dict(rxn.species[species].get_expression(scope_code))

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    out["species"] = species
    out["scope"] = name_for(scope_code, scope_codes)
    out["scope_code"] = scope_code
    return out


@reactions_mcp.tool()
async def set_species_expression(
    ctx: Context,
    session_id: str = "default",
    species: str | int = "",
    scope: str | int = "pipe",
    form: str | int = "rate",
    expression: str = "",
) -> dict:
    """Set (or clear) one species' reaction expression in one scope.

    ``scope`` is ``pipe`` or ``tank``. ``form`` is ``rate``, ``equil``,
    ``formula``, or ``none`` — ``none`` CLEARS the expression for that scope
    and ignores ``expression``.

    Pre-flight the text with ``reactions_validate_expression`` first: it
    compiles against the same vocabulary this call does and returns an error
    column, whereas a rejection here only tells you the write did not happen.
    The whole system is recompiled before this returns and the change is rolled
    back if anything fails to compile. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_expr_set
    if species == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a species name or index.")
    _, rxn = await _editable_reactions(ctx, session_id)
    scope_codes = reaction_scope_codes()
    form_codes = reaction_expr_form_codes()
    scope_code = coerce_enum(scope, scope_codes, "reaction scope")
    form_code = coerce_enum(form, form_codes, "reaction expression form")

    def _set() -> None:
        rxn.species[species].set_expression(scope_code, form_code, expression)

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "species": species,
        "scope": name_for(scope_code, scope_codes),
        "scope_code": scope_code,
        "form": name_for(form_code, form_codes),
        "form_code": form_code,
        "expression": expression,
    }


# ---------------------------------------------------------------------------
# Coefficients
# ---------------------------------------------------------------------------


@reactions_mcp.tool()
async def list_coefficients(ctx: Context, session_id: str = "default") -> dict:
    """List every ``[REACTION_COEFFICIENTS]`` entry.

    Each row carries ``index``, ``name``, ``is_param`` and ``value``.
    ``is_param`` false means a CONSTANT — one value for the whole model;
    ``is_param`` true means a PARAMETER, which may be varied per element.

    Coefficient names are part of the authoritative expression vocabulary.
    """
    # wraps: swmm_reaction_coeff_count swmm_reaction_coeff_get
    _, rxn = await _readable_reactions(ctx, session_id)

    def _read() -> dict:
        rows = [
            {
                "index": int(c.index),
                "name": str(c.name),
                "is_param": bool(c.is_param),
                "value": float(c.value),
            }
            for c in rxn.coefficients
        ]
        return {"count": len(rows), "coefficients": rows}

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@reactions_mcp.tool()
async def add_coefficient(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    parameter: bool = False,
    value: float = 0.0,
) -> dict:
    """Add a reaction coefficient.

    ``parameter=False`` declares a CONSTANT (one value model-wide);
    ``parameter=True`` a PARAMETER (may be varied per element). ``value`` is
    the initial value in whatever units the expressions using it imply — the
    reaction system does not attach units to coefficients.

    The system is recompiled and the addition rolled back on failure.
    BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_coeff_add
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a coefficient name.")
    _, rxn = await _editable_reactions(ctx, session_id)

    def _add() -> dict:
        coeff = rxn.coefficients.add(name, parameter=bool(parameter), value=float(value))
        return {"index": int(coeff.index), "name": str(coeff.name)}

    out = await asyncio.to_thread(_apply, _add)
    out["status"] = "ok"
    out["session_id"] = session_id
    out["is_param"] = bool(parameter)
    out["value"] = float(value)
    return out


@reactions_mcp.tool()
async def set_coefficient_value(
    ctx: Context,
    session_id: str = "default",
    coefficient: str | int = "",
    value: float = 0.0,
) -> dict:
    """Change a coefficient's value, by name or index.

    Changes the number only — the coefficient's constant/parameter kind and
    every expression referencing it are untouched, so this is the cheap knob
    for calibration sweeps. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_coeff_set_value
    if coefficient == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a coefficient name or index.")
    _, rxn = await _editable_reactions(ctx, session_id)
    val = float(value)

    def _set() -> None:
        rxn.coefficients[coefficient].value = val

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "coefficient": coefficient,
        "value": val,
    }


@reactions_mcp.tool()
async def remove_coefficient(
    ctx: Context,
    session_id: str = "default",
    coefficient: str | int = "",
) -> dict:
    """Remove a coefficient by name or index.

    REFUSED while any compiled expression still references it — rewrite those
    expressions first. Later indices shift down. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_coeff_remove
    if coefficient == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a coefficient name or index.")
    _, rxn = await _editable_reactions(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: rxn.coefficients.remove(coefficient))
    return {"status": "ok", "session_id": session_id, "coefficient": coefficient}


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------


@reactions_mcp.tool()
async def list_terms(ctx: Context, session_id: str = "default") -> dict:
    """List every ``[REACTION_TERMS]`` intermediate term.

    Each row carries ``index``, ``name`` and ``expression``. Terms are named
    sub-expressions that species expressions (and later terms) can reference,
    so a long rate law can be written once and reused.

    Term names are part of the authoritative expression vocabulary. Note the
    ordering rule: a term may only reference terms declared BEFORE it, and that
    rule is enforced when a whole file is applied (where ordinal position
    exists), not by ``reactions_validate_expression``.
    """
    # wraps: swmm_reaction_term_count swmm_reaction_term_get
    _, rxn = await _readable_reactions(ctx, session_id)

    def _read() -> dict:
        rows = [
            {"index": int(t.index), "name": str(t.name), "expression": str(t.expression)}
            for t in rxn.terms
        ]
        return {"count": len(rows), "terms": rows}

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@reactions_mcp.tool()
async def add_term(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    expression: str = "",
) -> dict:
    """Add an intermediate term.

    The term is appended, so it may reference every term already declared and
    can itself be referenced by terms and species expressions added afterwards
    — the forward-only ordering rule means a term can never reference one
    declared later.

    Pre-flight ``expression`` with ``reactions_validate_expression`` using
    ``scope="term"``. The system is recompiled and the addition rolled back on
    failure. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_term_add
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a term name.")
    _, rxn = await _editable_reactions(ctx, session_id)

    def _add() -> dict:
        term = rxn.terms.add(name, expression)
        return {"index": int(term.index), "name": str(term.name)}

    out = await asyncio.to_thread(_apply, _add)
    out["status"] = "ok"
    out["session_id"] = session_id
    out["expression"] = expression
    return out


@reactions_mcp.tool()
async def set_term_expression(
    ctx: Context,
    session_id: str = "default",
    term: str | int = "",
    expression: str = "",
) -> dict:
    """Replace one term's expression, by name or index.

    The term keeps its ordinal position, so the forward-only reference rule is
    evaluated against the same neighbours as before. Pre-flight with
    ``reactions_validate_expression`` (``scope="term"``). The whole system is
    recompiled and the change rolled back if anything that referenced this term
    stops compiling. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_term_set_expr
    if term == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a term name or index.")
    _, rxn = await _editable_reactions(ctx, session_id)

    def _set() -> None:
        rxn.terms[term].expression = expression

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "term": term,
        "expression": expression,
    }


@reactions_mcp.tool()
async def remove_term(
    ctx: Context,
    session_id: str = "default",
    term: str | int = "",
) -> dict:
    """Remove an intermediate term by name or index.

    REFUSED while any compiled expression still references it — rewrite those
    first. Later indices shift down. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_term_remove
    if term == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a term name or index.")
    _, rxn = await _editable_reactions(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: rxn.terms.remove(term))
    return {"status": "ok", "session_id": session_id, "term": term}


# ---------------------------------------------------------------------------
# [REACTION_OPTIONS]
# ---------------------------------------------------------------------------


@reactions_mcp.tool()
async def get_option(
    ctx: Context,
    session_id: str = "default",
    key: str = "",
) -> dict:
    """Read one ``[REACTION_OPTIONS]`` value as its canonical token.

    Recognised keys: ``SOLVER``, ``COUPLING``, ``RATE_UNITS``, ``AREA_UNITS``,
    ``TIMESTEP``, ``ATOL``, ``RTOL``. The value comes back as the engine's own
    canonical spelling, which is what ``reactions_set_option`` expects — so
    read-modify-write is safe without normalising anything yourself.

    ``ATOL`` / ``RTOL`` here are the GLOBAL tolerances; a species carrying its
    own non-zero ``atol`` / ``rtol`` overrides them (``reactions_list_species``).
    """
    # wraps: swmm_reaction_option_get
    if not key:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Provide a key (SOLVER, COUPLING, RATE_UNITS, "
            f"AREA_UNITS, TIMESTEP, ATOL or RTOL)."
        )
    _, rxn = await _readable_reactions(ctx, session_id)
    value = await asyncio.to_thread(_apply, lambda: rxn.get_option(key))
    return {"session_id": session_id, "key": key, "value": str(value)}


@reactions_mcp.tool()
async def set_option(
    ctx: Context,
    session_id: str = "default",
    key: str = "",
    value: str = "",
) -> dict:
    """Set one ``[REACTION_OPTIONS]`` value from its canonical token.

    Keys as for ``reactions_get_option``. ``value`` must be the canonical token
    the engine uses (read the current one back first if unsure); an
    unrecognised key or token is refused. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_option_set
    if not key:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a key.")
    _, rxn = await _editable_reactions(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: rxn.set_option(key, value))
    return {"status": "ok", "session_id": session_id, "key": key, "value": value}


# ---------------------------------------------------------------------------
# Initial quality (global + per-element)
# ---------------------------------------------------------------------------


@reactions_mcp.tool()
async def list_initial_quality(ctx: Context, session_id: str = "default") -> dict:
    """List the reaction system's initial-quality seeds, global and per-element.

    ``globals`` carries one row per species with the GLOBAL initial
    concentration applied everywhere it is not overridden. ``elements`` carries
    the per-element rows: ``row_index`` (the handle
    ``reactions_remove_initial_element`` takes), ``is_link`` (false = node),
    ``elem_index``, ``species_index`` and ``value``.

    This is the reaction system's own species table. Pollutant concentrations,
    water age and temperature are seeded through the separate
    ``[INITIAL_QUALITY]`` section — see ``initial_quality_list_entries``.
    """
    # wraps: swmm_reaction_init_global_get swmm_reaction_init_elem_count swmm_reaction_init_elem_get  # noqa: E501
    _, rxn = await _readable_reactions(ctx, session_id)

    def _read() -> dict:
        globals_rows = [
            {
                "species_index": int(s.index),
                "species": str(s.name),
                "value": float(s.initial),
            }
            for s in rxn.species
        ]
        rows = []
        for row_index in range(len(rxn.initial)):
            entry = rxn.initial[row_index]
            rows.append(
                {
                    "row_index": row_index,
                    "is_link": bool(entry.is_link),
                    "elem_index": int(entry.elem_index),
                    "species_index": int(entry.species_index),
                    "value": float(entry.value),
                }
            )
        return {
            "globals": globals_rows,
            "count": len(rows),
            "elements": rows,
        }

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@reactions_mcp.tool()
async def set_initial_global(
    ctx: Context,
    session_id: str = "default",
    species: str | int = "",
    value: float = 0.0,
) -> dict:
    """Set one species' GLOBAL initial concentration.

    Applies everywhere the species is not given a per-element seed by
    ``reactions_set_initial_element``. ``value`` is in the species' own declared
    units (``reactions_list_species``). BUILDING/OPENED only, because initial
    values are consumed once at initialize().
    """
    # wraps: swmm_reaction_init_global_set
    if species == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a species name or index.")
    _, rxn = await _editable_reactions(ctx, session_id)
    val = float(value)

    def _set() -> None:
        rxn.species[species].initial = val

    await asyncio.to_thread(_apply, _set)
    return {"status": "ok", "session_id": session_id, "species": species, "value": val}


@reactions_mcp.tool()
async def set_initial_element(
    ctx: Context,
    session_id: str = "default",
    species: str | int = "",
    elem_index: int = -1,
    is_link: bool = False,
    value: float = 0.0,
) -> dict:
    """Seed one node or link with a species' initial concentration.

    ``is_link=False`` addresses a node, ``True`` a link; ``elem_index`` is the
    element's integer index. ``value`` is in the species' own units and must be
    NON-NEGATIVE.

    This is an UPSERT on (is_link, elem_index, species): writing the same triple
    again edits the existing row rather than adding a second one. Overrides the
    global value from ``reactions_set_initial_global`` for that element.
    BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_init_elem_set
    if species == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a species name or index.")
    if elem_index < 0:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a non-negative elem_index.")
    _, rxn = await _editable_reactions(ctx, session_id)
    val = float(value)

    def _set() -> None:
        rxn.initial.set(bool(is_link), int(elem_index), species, val)

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "species": species,
        "elem_index": int(elem_index),
        "is_link": bool(is_link),
        "value": val,
    }


@reactions_mcp.tool()
async def remove_initial_element(
    ctx: Context,
    session_id: str = "default",
    row_index: int = -1,
) -> dict:
    """Remove one per-element initial-quality row by position.

    ``row_index`` is the 0-based position from
    ``reactions_list_initial_quality``. Later rows shift DOWN by one, so
    re-enumerate between removals. The element falls back to the species'
    global initial value. BUILDING/OPENED only.
    """
    # wraps: swmm_reaction_init_elem_remove
    if row_index < 0:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a non-negative row_index.")
    _, rxn = await _editable_reactions(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: rxn.initial.remove(int(row_index)))
    return {"status": "ok", "session_id": session_id, "row_index": int(row_index)}


# ---------------------------------------------------------------------------
# Validation and whole-file text
# ---------------------------------------------------------------------------


@reactions_mcp.tool()
async def validate_expression(
    ctx: Context,
    session_id: str = "default",
    expression: str = "",
    scope: str | int = "pipe",
) -> dict:
    """Compile-check ONE expression against the model's live vocabulary.

    **Changes nothing**, whether the expression compiles or not — this is the
    safe pre-flight to run before ``reactions_set_species_expression`` or
    ``reactions_set_term_expression`` rather than submitting blind and reading
    the refusal.

    ``scope`` selects the identifier vocabulary: ``pipe``, ``tank`` or
    ``term``. Returns ``valid``, ``message`` (the engine's own diagnostic,
    empty on success) and ``column`` — a 1-BASED error column, or ``-1`` when
    the error is not attributable to one position.

    One caveat under ``scope="term"``: references to ALL terms are accepted,
    including ones declared later. The forward-only ordering rule is enforced
    when a whole file is applied, where ordinal position exists. This tool
    answers "is this well-formed against the vocabulary", not "is the file
    orderable".
    """
    # wraps: swmm_reaction_validate_expression
    if not expression:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide an expression.")
    _, rxn = await _readable_reactions(ctx, session_id)
    scope_codes = reaction_scope_codes()
    scope_code = coerce_enum(scope, scope_codes, "reaction scope")

    diag = await asyncio.to_thread(_apply, lambda: rxn.validate(expression, scope_code))
    return {
        "session_id": session_id,
        "expression": expression,
        "scope": name_for(scope_code, scope_codes),
        "scope_code": scope_code,
        "valid": bool(diag.valid),
        "message": str(diag.message),
        "column": int(diag.column),
    }


@reactions_mcp.tool()
async def serialize(ctx: Context, session_id: str = "default") -> dict:
    """Return the whole reaction system as canonical ``.rxn`` text.

    ``serialize`` -> edit -> ``reactions_apply_text`` round-trips
    byte-identically, so this is the head of a safe whole-file editing loop:
    read the text, change it, dry-run it with ``reactions_check_text``, then
    apply it.

    Reflects engine state, not any file on disk. To write it out instead, use
    ``reactions_save``.
    """
    # wraps: swmm_reactions_serialize
    _, rxn = await _readable_reactions(ctx, session_id)
    text = await asyncio.to_thread(_apply, rxn.serialize)
    return {"session_id": session_id, "text": str(text), "length": len(str(text))}


@reactions_mcp.tool()
async def check_text(
    ctx: Context,
    session_id: str = "default",
    text: str = "",
) -> dict:
    """Dry-run a whole ``.rxn`` file against this model without changing anything.

    A full parse and compile with ZERO state change on success or on failure —
    the safe probe to run before ``reactions_apply_text``, which does mutate.

    Returns ``valid`` and ``message`` (the engine's first diagnostic). Unlike
    ``reactions_validate_expression``, ``column`` is always ``-1``: the
    whole-file checker reports its diagnostic as text without a column.

    Unlike the single-expression check, this DOES enforce the forward-only
    term-ordering rule, because a file has ordinal positions.
    """
    # wraps: swmm_reactions_check_text
    if not text:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide .rxn text to check.")
    _, rxn = await _readable_reactions(ctx, session_id)
    diag = await asyncio.to_thread(_apply, lambda: rxn.check_text(text))
    return {
        "session_id": session_id,
        "valid": bool(diag.valid),
        "message": str(diag.message),
        "column": int(diag.column),
    }


@reactions_mcp.tool()
async def apply_text(
    ctx: Context,
    session_id: str = "default",
    text: str = "",
) -> dict:
    """Transactionally replace the whole reaction system from ``.rxn`` text.

    Staged: on ANY error the previous system — reaction state and registry
    block alike — is byte-identical to what it was before the call, so a
    rejected edit costs nothing. Run ``reactions_check_text`` first if you want
    the diagnostic without attempting the write.

    On success EVERY index is invalidated: species, coefficient, term and
    initial-row positions may all have moved, so re-enumerate rather than
    reusing cached indices. BUILDING/OPENED only.
    """
    # wraps: swmm_reactions_apply_text
    if not text:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide .rxn text to apply.")
    _, rxn = await _editable_reactions(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: rxn.apply_text(text))
    return {"status": "ok", "session_id": session_id, "length": len(text)}


@reactions_mcp.tool()
async def save(
    ctx: Context,
    session_id: str = "default",
    path: str = "",
) -> dict:
    """Write the reaction system to a ``.rxn`` file on disk.

    With ``path`` empty the file goes to the path bound to the reaction
    component's ``[PROCESS_COMPONENTS]`` config entry; if nothing is bound
    there and no ``path`` is given, the call is refused because there is
    nowhere to write. Check the binding with
    ``process_components_list_components``.
    """
    # wraps: swmm_reactions_save
    _, rxn = await _readable_reactions(ctx, session_id)
    target = path or None
    await asyncio.to_thread(_apply, lambda: rxn.save(target))
    return {"status": "ok", "session_id": session_id, "path": path or "<component config path>"}


# ---------------------------------------------------------------------------
# Static vocabulary -- NO session, NO open model
# ---------------------------------------------------------------------------


@reactions_mcp.tool()
async def hydraulic_variables(ctx: Context) -> dict:
    """List the built-in hydraulic variables a reaction expression may reference.

    **Takes no session and needs no open model** — this is an engine-less
    static read straight from the expression compiler's own table, so it works
    before anything is loaded and cannot drift from what actually compiles.

    Each row carries ``name`` and ``description``. These are the flow-state
    identifiers (depth, flow, velocity, Reynolds number, shear, friction,
    hydraulic radius, residence time, timestep and so on) available alongside
    the model's species, coefficients and terms. Use this as the completer
    vocabulary rather than hard-coding a list.
    """
    # wraps: swmm_reaction_hydvar_count swmm_reaction_hydvar_get

    def _read() -> dict:
        from openswmm.engine import Reactions

        rows = [
            {"name": str(v.name), "description": str(v.description)}
            for v in Reactions.hydraulic_variables()
        ]
        return {"count": len(rows), "hydraulic_variables": rows}

    try:
        return await asyncio.to_thread(_read)
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.NOT_SUPPORTED}] The openswmm engine is not available: {exc}"
        ) from exc


@reactions_mcp.tool()
async def functions(ctx: Context) -> dict:
    """List the built-in functions a reaction expression may call.

    **Takes no session and needs no open model** — like
    ``reactions_hydraulic_variables``, an engine-less static read from the
    expression compiler's own table.

    Each row carries ``name`` and ``arity`` (how many arguments the function
    takes). Use this as the authoritative completer vocabulary rather than
    assuming a function exists; ``reactions_validate_expression`` is the way to
    confirm a full expression once written.
    """
    # wraps: swmm_reaction_function_count swmm_reaction_function_get

    def _read() -> dict:
        from openswmm.engine import Reactions

        rows = [{"name": str(f.name), "arity": int(f.arity)} for f in Reactions.functions()]
        return {"count": len(rows), "functions": rows}

    try:
        return await asyncio.to_thread(_read)
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.NOT_SUPPORTED}] The openswmm engine is not available: {exc}"
        ) from exc
