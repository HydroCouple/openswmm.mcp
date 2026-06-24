"""Gym tools: Pareto filtering, quality indicators, and the model bridge.

Phase 5 of C{docs/developer/GYMNASIUM_INTEGRATION_PLAN.md}. Registers
onto the shared C{gym_mcp} sub-server (C{gym_} namespace).

  - L{pareto_filter} / L{score_front} / L{compare_runs} — thin, argument-
    checked wrappers over C{openswmm_gymnasium.scoring} so fronts from
    optimization jobs (or supplied inline) can be assessed and compared
    in natural language.
  - L{apply_design} — closes the loop: applies a chosen decision vector
    from a finished job onto an open MCP model session using the same
    engine surface the design factories use, so the user can then call
    C{building_write_model} / rerun analysis with existing tools.

All objective vectors here are direction-adjusted B{costs to minimize},
exactly as produced by C{gym_get_job_results}.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: MIT
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
from fastmcp import Context

from openswmm_mcp.dependencies import (
    get_job_manager,
    get_session_manager,
    require_gymnasium,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.gym_support.envs import json_safe
from openswmm_mcp.gym_support.jobs import design_dimensions
from openswmm_mcp.tools.gym_envs import gym_mcp

#: Indicator name -> required keyword arguments (beyond the front itself).
_INDICATOR_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "hypervolume": ("reference_point",),
    "normalized_hypervolume": ("ideal_point", "reference_point"),
    "igd": ("reference_front",),
    "igd_plus": ("reference_front",),
    "epsilon_indicator": ("reference_front",),
    "spread": (),
    "r2_indicator": ("weights", "reference_point"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_matrix(name: str, rows: list[list[float]] | None) -> np.ndarray:
    """Convert a list-of-vectors argument into a 2-D float array.

    @param name: Argument name for error messages.
    @type name: str
    @param rows: The raw vectors.
    @type rows: list of list of float, or C{None}
    @return: Array of shape C{(n, n_objectives)}.
    @rtype: L{numpy.ndarray}
    @raise ToolError: C{VALIDATION_ERROR} when empty or ragged.
    """
    try:
        matrix = np.asarray(rows, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] '{name}' must be a list of "
            f"equal-length numeric vectors: {exc}"
        ) from exc
    if matrix.ndim != 2 or matrix.size == 0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] '{name}' must be a non-empty "
            f"list of equal-length numeric vectors."
        )
    return matrix


async def _resolve_front(
    ctx: Context,
    front: list[list[float]] | None,
    job_id: str | None,
) -> tuple[np.ndarray, list[str] | None]:
    """Resolve an objective matrix from inline vectors or a finished job.

    @param ctx: FastMCP request context.
    @type ctx: L{Context}
    @param front: Inline objective vectors, or C{None}.
    @type front: list of list of float, or C{None}
    @param job_id: Finished job whose Pareto front to use, or C{None}.
    @type job_id: str or C{None}
    @return: C{(matrix, objective_names)}; names are C{None} for inline
        fronts.
    @rtype: tuple
    @raise ToolError: C{VALIDATION_ERROR} unless exactly one source is
        given; job-state errors propagate from the manager.
    """
    if (front is None) == (job_id is None):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Pass exactly one of 'front' "
            "(inline objective vectors) or 'job_id' (a finished job)."
        )
    if front is not None:
        return _as_matrix("front", front), None
    results = await asyncio.to_thread(get_job_manager(ctx).results, job_id)
    names = results["objective_names"]
    matrix = _as_matrix(
        "job pareto front",
        [[e["objectives"][n] for n in names] for e in results["pareto"]],
    )
    return matrix, names


def _check_indicator_args(indicator: str, provided: dict[str, Any]) -> None:
    """Ensure *indicator* exists and its required args are present.

    @raise ToolError: C{VALIDATION_ERROR} listing what is missing.
    """
    if indicator not in _INDICATOR_REQUIREMENTS:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown indicator '{indicator}'. "
            f"Valid indicators: {', '.join(sorted(_INDICATOR_REQUIREMENTS))}."
        )
    missing = [
        arg for arg in _INDICATOR_REQUIREMENTS[indicator] if provided.get(arg) is None
    ]
    if missing:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Indicator '{indicator}' requires "
            f"{', '.join(missing)}."
        )


def _compute_indicator(
    indicator: str,
    matrix: np.ndarray,
    *,
    reference_point: list[float] | None,
    ideal_point: list[float] | None,
    reference_front: list[list[float]] | None,
    weights: list[list[float]] | None,
) -> float:
    """Compute one indicator value over *matrix* (costs to minimize).

    @return: The indicator value.
    @rtype: float
    """
    from openswmm_gymnasium import scoring

    if indicator == "hypervolume":
        return float(scoring.hypervolume(matrix, np.asarray(reference_point)))
    if indicator == "normalized_hypervolume":
        return float(
            scoring.normalized_hypervolume(
                matrix, np.asarray(ideal_point), np.asarray(reference_point)
            )
        )
    if indicator == "igd":
        return float(scoring.igd(matrix, _as_matrix("reference_front", reference_front)))
    if indicator == "igd_plus":
        return float(scoring.igd_plus(matrix, _as_matrix("reference_front", reference_front)))
    if indicator == "epsilon_indicator":
        return float(
            scoring.epsilon_indicator(matrix, _as_matrix("reference_front", reference_front))
        )
    if indicator == "spread":
        return float(scoring.spread(matrix))
    # r2_indicator
    return float(
        scoring.r2_indicator(
            matrix, _as_matrix("weights", weights), np.asarray(reference_point)
        )
    )


# ---------------------------------------------------------------------------
# Scoring tools
# ---------------------------------------------------------------------------


@gym_mcp.tool()
async def pareto_filter(
    ctx: Context,
    front: list[list[float]] | None = None,
    job_id: str | None = None,
) -> dict:
    """Return the non-dominated subset of objective vectors.

    Pass either I{front} (inline list of cost vectors, minimize) or
    I{job_id} (a finished optimization job — its already-Pareto front is
    re-filtered, which is a cheap no-op check). Returns the front and
    the indices of the surviving rows. Requires the gym extra.
    """
    require_gymnasium("gym_pareto_filter")
    matrix, names = await _resolve_front(ctx, front, job_id)

    from openswmm_gymnasium.scoring import pareto_front

    filtered = await asyncio.to_thread(pareto_front, matrix)
    indices = [
        i for i, row in enumerate(matrix) if any(np.allclose(row, f) for f in filtered)
    ]
    result = {
        "input_count": int(matrix.shape[0]),
        "front": json_safe(filtered),
        "indices": indices,
    }
    if names is not None:
        result["objective_names"] = names
    return result


@gym_mcp.tool()
async def score_front(
    ctx: Context,
    indicators: list[str],
    front: list[list[float]] | None = None,
    job_id: str | None = None,
    reference_point: list[float] | None = None,
    ideal_point: list[float] | None = None,
    reference_front: list[list[float]] | None = None,
    weights: list[list[float]] | None = None,
) -> dict:
    """Compute quality indicators over a front of cost vectors.

    Available indicators and their required arguments:

      - C{hypervolume}: I{reference_point} (nadir / worst-case).
      - C{normalized_hypervolume}: I{ideal_point} + I{reference_point}.
      - C{igd}, C{igd_plus}, C{epsilon_indicator}: I{reference_front}.
      - C{spread}: no extra arguments.
      - C{r2_indicator}: I{weights} (list of weight vectors) +
        I{reference_point}.

    The front comes from I{front} (inline) or I{job_id} (finished job),
    exactly one. Requires the gym extra.
    """
    require_gymnasium("gym_score_front")
    provided = {
        "reference_point": reference_point,
        "ideal_point": ideal_point,
        "reference_front": reference_front,
        "weights": weights,
    }
    if not indicators:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] 'indicators' must name at least "
            f"one of: {', '.join(sorted(_INDICATOR_REQUIREMENTS))}."
        )
    for indicator in indicators:
        _check_indicator_args(indicator, provided)
    matrix, names = await _resolve_front(ctx, front, job_id)

    def _compute_all() -> dict[str, float]:
        return {
            indicator: _compute_indicator(
                indicator,
                matrix,
                reference_point=reference_point,
                ideal_point=ideal_point,
                reference_front=reference_front,
                weights=weights,
            )
            for indicator in indicators
        }

    scores = await asyncio.to_thread(_compute_all)
    result: dict[str, Any] = {"front_size": int(matrix.shape[0]), "scores": scores}
    if names is not None:
        result["objective_names"] = names
    if job_id is not None:
        result["job_id"] = job_id
    return result


@gym_mcp.tool()
async def compare_runs(
    ctx: Context,
    job_ids: list[str],
    indicators: list[str],
    reference_point: list[float] | None = None,
    ideal_point: list[float] | None = None,
    reference_front: list[list[float]] | None = None,
    weights: list[list[float]] | None = None,
) -> dict:
    """Score several finished jobs' Pareto fronts with the same indicators.

    Returns one row per job (front size + each indicator value) so runs
    of different algorithms, budgets, or scenarios can be compared
    directly. Argument requirements per indicator match
    C{gym_score_front}. Requires the gym extra.
    """
    require_gymnasium("gym_compare_runs")
    if len(job_ids) < 2:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] compare_runs needs at least two "
            "job_ids; use gym_score_front for a single run."
        )
    rows = []
    for job_id in job_ids:
        scored = await score_front(
            ctx,
            indicators=indicators,
            job_id=job_id,
            reference_point=reference_point,
            ideal_point=ideal_point,
            reference_front=reference_front,
            weights=weights,
        )
        rows.append(
            {
                "job_id": job_id,
                "front_size": scored["front_size"],
                **scored["scores"],
            }
        )
    return {"indicators": indicators, "runs": rows}


# ---------------------------------------------------------------------------
# Model bridge
# ---------------------------------------------------------------------------


def _pick_evaluation(results: dict[str, Any], output_dir: str, evaluation: int | str):
    """Select one evaluation record from a finished job's results.

    @param results: Payload from C{JobManager.results}.
    @type results: dict
    @param output_dir: The job's artifact directory (for the full log).
    @type output_dir: str
    @param evaluation: C{"best"}, or a 1-based evaluation number.
    @type evaluation: int or str
    @return: The chosen evaluation record.
    @rtype: dict
    @raise ToolError: C{VALIDATION_ERROR} / C{ELEMENT_NOT_FOUND}.
    """
    if evaluation == "best":
        record = results.get("best")
        if record is None:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] Job has no evaluations."
            )
        return record
    if not isinstance(evaluation, int):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] 'evaluation' must be \"best\" or "
            "a 1-based evaluation number."
        )
    log_path = Path(output_dir) / "evaluations.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("evaluation") == evaluation:
                return record
    raise ToolError(
        f"[{ErrorCode.ELEMENT_NOT_FOUND}] No evaluation #{evaluation} in "
        f"{log_path}."
    )


def _apply_market_policy(
    env_config: Any,
    decisions: dict[str, list[float]],
    record: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Write a market job's optimized controller config to its output dir.

    Rebuilds the tuned C{MarketConfig} from the chosen decision vector and saves
    it as C{market_config.tuned.json} (user-reviewable). No model session is
    touched — a market job tunes the controller, not the model.

    @return: The applied policy, objectives, and the written config path.
    @rtype: dict
    """
    from openswmm_gymnasium.config import MarketConfig
    from openswmm_gymnasium.spaces import MarketPolicySpace

    market = MarketConfig.from_dict(env_config.market_config)
    market.validate()
    bounds = (
        {k: tuple(v) for k, v in env_config.policy_bounds.items()}
        if env_config.policy_bounds
        else None
    )
    space = MarketPolicySpace.from_config(market, bounds=bounds, tune_full=env_config.tune_full)
    vector = np.array([float(decisions[label][0]) for label in space.labels], dtype=np.float64)
    tuned = space.unflatten(vector)
    tuned.validate()

    out_path = output_dir / "market_config.tuned.json"
    tuned.save(str(out_path))
    return {
        "applied": "market_policy",
        "evaluation": record.get("evaluation"),
        "market_config_path": str(out_path),
        "objectives": record.get("objectives", {}),
        "policy": {label: float(decisions[label][0]) for label in space.labels},
    }


def _apply_schedule_policy(
    env_config: Any,
    decisions: dict[str, list[float]],
    record: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Write a schedule job's optimized control schedule to its output dir.

    Saves C{schedule.tuned.json} (the per-structure setting schedule + cadence).
    No model session is touched.

    @return: The applied schedule, objectives, and the written path.
    @rtype: dict
    """
    from openswmm_gymnasium.spaces import SchedulePolicySpace

    lo, hi = env_config.schedule_bounds or [0.0, 1.0]
    space = SchedulePolicySpace(
        env_config.structure_ids, env_config.n_points, low=float(lo), high=float(hi)
    )
    vector = np.array([float(decisions[label][0]) for label in space.labels], dtype=np.float64)
    schedule = space.unflatten(vector)

    out_path = output_dir / "schedule.tuned.json"
    out_path.write_text(
        json.dumps(
            {
                "control_interval_seconds": env_config.control_interval_seconds,
                "structure_ids": list(env_config.structure_ids),
                "n_points": env_config.n_points,
                "schedule": schedule,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "applied": "schedule_policy",
        "evaluation": record.get("evaluation"),
        "schedule_path": str(out_path),
        "objectives": record.get("objectives", {}),
        "schedule": schedule,
    }


@gym_mcp.tool()
async def apply_design(
    ctx: Context,
    job_id: str,
    session_id: str = "default",
    evaluation: int | str = "best",
) -> dict:
    """Apply a job's optimized design vector to an open model session.

    Picks I{evaluation} (C{"best"} by default, or a 1-based evaluation
    number from the job log) and writes its decision values onto the
    session's model elements through the same engine surface the design
    factories use: link roughness, link length, conduit diameter
    (xsect geom1), and node maximum depth, each clipped to the factory's
    [low, high] design range.

    The session must be open or initialized on the new engine. The
    change is in-memory — call C{building_write_model} to persist a new
    C{.inp}, or rerun the simulation to evaluate it. Requires the gym
    extra.

    For a C{"market"} job (operational tuning) there is no model edit: the
    optimized controller config is written to C{market_config.tuned.json} in
    the job's output dir and its path is returned; C{session_id} is ignored.
    A C{"schedule"} job similarly writes C{schedule.tuned.json} (the optimized
    open-loop control schedule).
    """
    require_gymnasium("gym_apply_design")
    manager = get_job_manager(ctx)
    results = await asyncio.to_thread(manager.results, job_id)
    env_config = manager.get_env_config(job_id)
    record = _pick_evaluation(results, results["output_dir"], evaluation)
    decisions: dict[str, list[float]] = record["decisions"]

    # market/schedule jobs tune a controller, not the model: write the optimized
    # config to the job's output dir rather than editing a session.
    if env_config.env_type == "market":
        return await asyncio.to_thread(
            _apply_market_policy, env_config, decisions, record, Path(results["output_dir"])
        )
    if env_config.env_type == "schedule":
        return await asyncio.to_thread(
            _apply_schedule_policy, env_config, decisions, record, Path(results["output_dir"])
        )

    session_manager = get_session_manager(ctx)
    session = await session_manager.get_session(session_id)
    require_new_engine(session, "gym_apply_design")
    require_state(session, "opened", "initialized", "building")

    # Design edits use the engine's pre-initialize edit surface (link
    # roughness / node max depth), which the C core rejects once a solver has
    # been initialized.  ``lifecycle_open_model`` leaves the session
    # "initialized", so re-open a fresh editable solver on the same model
    # before applying — making the documented open -> apply -> write workflow
    # work end to end.  A "building" / "opened" session is already editable.
    if session.state == "initialized" and session.backend is not None:
        from openswmm_mcp.backends import make_backend

        engine_kind = session.engine_kind

        def _reopen_editable() -> None:
            backend = make_backend(
                engine_kind,
                session.inp_path,
                session.rpt_path,
                session.out_path,
            )
            backend.solver.open()
            session.backend = backend
            session.state = "opened"
            session._meta = None

        await asyncio.to_thread(_reopen_editable)

    dims = design_dimensions(env_config)
    applied: list[dict[str, Any]] = []

    def _apply_all() -> None:
        links = session.links
        nodes = session.nodes
        for spec, dim in zip(env_config.design_factories, dims):
            values = np.clip(
                np.asarray(decisions[dim.key], dtype=np.float64), dim.low, dim.high
            )
            ids = spec.params.get("link_ids") or spec.params.get("node_ids") or []
            for element_id, value in zip(ids, values):
                value = float(value)
                if spec.kind in ("link_roughness", "link_length", "link_diameter"):
                    idx = links.get_index(element_id)
                    if idx < 0:
                        raise ToolError(
                            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link "
                            f"'{element_id}' not found in session '{session_id}'."
                        )
                    link = links[idx]
                    if spec.kind == "link_roughness":
                        link.roughness = value
                    elif spec.kind == "link_length":
                        link.length = value
                    else:  # link_diameter: overwrite xsect geom1, keep the rest
                        shape, _g1, g2, g3, g4 = tuple(link.xsect)
                        link.xsect = (shape, value, g2, g3, g4)
                else:  # node_max_depth
                    idx = nodes.get_index(element_id)
                    if idx < 0:
                        raise ToolError(
                            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node "
                            f"'{element_id}' not found in session '{session_id}'."
                        )
                    nodes[idx].max_depth = value
                applied.append(
                    {"kind": spec.kind, "element_id": element_id, "value": value}
                )

    await asyncio.to_thread(_apply_all)
    return {
        "job_id": job_id,
        "session_id": session_id,
        "evaluation": record.get("evaluation", "best"),
        "objectives": record.get("objectives"),
        "applied": applied,
        "next": "Call building_write_model to persist a new .inp, or rerun "
        "the simulation to evaluate the applied design.",
    }
