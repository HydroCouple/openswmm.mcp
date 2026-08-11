"""Background optimization jobs over design spaces (plan Phase 4).

A L{JobManager} owns a small thread pool (default C{max_workers=2},
plan §7.3 — each job holds one Solver and is CPU-heavy) running design
searches over C{cip}/C{joint} env configs:

  - C{random_search} — uniform sampling baseline.
  - C{grid_search}   — full factorial over per-dimension levels,
    truncated at the budget.
  - C{nsga2} (or any Platypus MOEA by class name) — multi-objective
    design search via the optional C{platypus-opt} extra.

One evaluation = one episode: for C{cip} the env is a single-step
contextual bandit (design applied in C{step}); for C{joint} the design
is fixed at C{reset(options={"design_action": ...})} and the runtime
half runs a neutral midpoint action each step.

Cancellation is cooperative — checked between evaluations. Every job
writes reviewable artifacts (C{job.json}, C{evaluations.jsonl},
C{result.json}) under a user-visible output directory (CLAUDE.md §4.1,
plan §3.4).

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: MIT
"""

from __future__ import annotations

import itertools
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.gym_support.config import EnvConfig, build_env
from openswmm_mcp.gym_support.envs import build_action, json_safe

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class OptimizationConfig(BaseModel):
    """Declarative optimization-run settings.

    @ivar algorithm: C{"random_search"}, C{"grid_search"}, or a Platypus
        MOEA class name in lowercase (e.g. C{"nsga2"}).
    @ivar budget: Maximum design evaluations for the job.
    @ivar population_size: MOEA population size (Platypus only).
    @ivar grid_levels: Per-dimension levels for C{grid_search}.
    @ivar seed: RNG seed for sampling algorithms and episode resets.
    @ivar max_steps_per_episode: Safety cap per evaluation episode.
    """

    model_config = ConfigDict(extra="forbid")

    algorithm: str = "random_search"
    budget: int = Field(default=50, ge=1)
    population_size: int = Field(default=20, ge=2)
    grid_levels: int = Field(default=5, ge=2)
    seed: int | None = None
    max_steps_per_episode: int = Field(default=10_000, ge=1)

    def resolved(self) -> dict[str, Any]:
        """The search settings actually applied, for echoing in every job
        snapshot and result.

        Surfacing these makes a silent algorithm/budget downgrade impossible to
        miss: a caller who reads the start response or C{gym_get_job} sees the
        real C{algorithm}/C{budget} the run used, not just whatever it
        requested (previously these lived only in C{job.json}). Only the fields
        that affect the chosen algorithm are included.

        @rtype: dict
        """
        out: dict[str, Any] = {
            "algorithm": self.algorithm,
            "budget": self.budget,
            "seed": self.seed,
        }
        if self.algorithm in _PLATYPUS_ALGORITHMS:
            out["population_size"] = self.population_size
        elif self.algorithm == "grid_search":
            out["grid_levels"] = self.grid_levels
        return out


JobState = Literal["pending", "running", "done", "failed", "cancelled"]

#: Platypus MOEA class names accepted as ``algorithm`` (lowercase key).
_PLATYPUS_ALGORITHMS = {
    "nsga2": "NSGAII",
    "nsga3": "NSGAIII",
    "spea2": "SPEA2",
    "moead": "MOEAD",
    "gde3": "GDE3",
}


# ---------------------------------------------------------------------------
# Design-space metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesignDimension:
    """One design factory's slice of the search space.

    @ivar key: Action-space key (the factory's C{name} param).
    @ivar labels: Per-component labels, C{"<kind>:<element_id>"}.
    @ivar low: Lower bound — a scalar broadcast across all components, or a
        per-component tuple (for factories whose components carry different
        physical ranges, e.g. storage C{(a,b,c)} or RDII C{(R,dmax,drecov,dinit)}).
    @ivar high: Upper bound, scalar or per-component like C{low}.
    """

    key: str
    labels: tuple[str, ...]
    low: float | tuple[float, ...]
    high: float | tuple[float, ...]

    @property
    def size(self) -> int:
        """Number of components in this dimension group.

        @rtype: int
        """
        return len(self.labels)


def _market_dimensions(env_config: EnvConfig) -> list[DesignDimension]:
    """Derive search dimensions from a market env's policy space.

    Each tunable controller parameter (cost-curve onset/steepness/ceiling, PID
    gains) becomes a single-component dimension carrying its own bounds.

    @param env_config: A C{"market"} env config.
    @type env_config: L{EnvConfig}
    @rtype: list of L{DesignDimension}
    @raise ToolError: C{DEPENDENCY_MISSING} without the gym extra.
    """
    try:
        from openswmm_gymnasium.config import MarketConfig
        from openswmm_gymnasium.spaces import MarketPolicySpace
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.DEPENDENCY_MISSING}] Market optimization requires the "
            "optional openswmm.gymnasium package. Install it with: "
            "pip install 'openswmm.mcp[gym]'"
        ) from exc

    market = MarketConfig.from_dict(env_config.market_config)
    market.validate()
    bounds = (
        {k: tuple(v) for k, v in env_config.policy_bounds.items()}
        if env_config.policy_bounds
        else None
    )
    space = MarketPolicySpace.from_config(market, bounds=bounds, tune_full=env_config.tune_full)
    return [
        DesignDimension(key=p.label, labels=(p.label,), low=p.low, high=p.high)
        for p in space.params
    ]


def _schedule_dimensions(env_config: EnvConfig) -> list[DesignDimension]:
    """Derive search dimensions from an open-loop control schedule.

    Each per-structure scheduled setting becomes a single-component dimension.

    @param env_config: A C{"schedule"} env config.
    @type env_config: L{EnvConfig}
    @rtype: list of L{DesignDimension}
    @raise ToolError: C{DEPENDENCY_MISSING} without the gym extra.
    """
    try:
        from openswmm_gymnasium.spaces import SchedulePolicySpace
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.DEPENDENCY_MISSING}] Schedule optimization requires the "
            "optional openswmm.gymnasium package. Install it with: "
            "pip install 'openswmm.mcp[gym]'"
        ) from exc

    lo, hi = env_config.schedule_bounds or [0.0, 1.0]
    space = SchedulePolicySpace(
        env_config.structure_ids, env_config.n_points, low=float(lo), high=float(hi)
    )
    return [
        DesignDimension(
            key=label, labels=(label,), low=float(space.low[i]), high=float(space.high[i])
        )
        for i, label in enumerate(space.labels)
    ]


def _control_curve_dimensions(env_config: EnvConfig) -> list[DesignDimension]:
    """Derive search dimensions from a reactive PWL control-curve policy.

    Each per-knot setting becomes a single-component dimension bounded by its
    asset's C{[y_low, y_high]} and labeled C{control_curve/<link_id>/y[<k>]}, so
    a job's decision vector decodes straight back to per-asset curves.

    @param env_config: A C{"control_curve"} env config.
    @type env_config: L{EnvConfig}
    @rtype: list of L{DesignDimension}
    @raise ToolError: C{DEPENDENCY_MISSING} without the gym extra.
    """
    try:
        from openswmm_gymnasium.spaces import ControlCurvePolicySpace
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.DEPENDENCY_MISSING}] Control-curve optimization requires "
            "the optional openswmm.gymnasium package. Install it with: "
            "pip install 'openswmm.mcp[gym]'"
        ) from exc

    space = ControlCurvePolicySpace.from_params(env_config.policy_factory.params)
    low, high = space.low, space.high
    return [
        DesignDimension(key=label, labels=(label,), low=float(low[i]), high=float(high[i]))
        for i, label in enumerate(space.labels)
    ]


def design_dimensions(env_config: EnvConfig) -> list[DesignDimension]:
    """Derive labeled search-space dimensions from the config.

    For C{"market"}/C{"schedule"}/C{"control_curve"} configs the dimensions
    come from the controller policy space; otherwise from the design factories.

    @param env_config: Config whose factories / policy span the space.
    @type env_config: L{EnvConfig}
    @return: One entry per factory (or policy parameter), in config order.
    @rtype: list of L{DesignDimension}
    @raise ToolError: C{VALIDATION_ERROR} when the config has no searchable
        static factory (no design_factory and no policy_factory).
    """
    if env_config.env_type == "market":
        return _market_dimensions(env_config)
    if env_config.env_type == "schedule":
        return _schedule_dimensions(env_config)
    if env_config.env_type == "control_curve":
        return _control_curve_dimensions(env_config)
    if not env_config.design_factories:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Optimization requires an env config "
            "with a searchable static factory: design_factories (env_type 'cip' "
            "or 'joint') or a policy_factory (env_type 'control_curve')."
        )
    dims: list[DesignDimension] = []
    for spec in env_config.design_factories:
        # Construct the factory (no solver needed — the Box is built in
        # __init__) and read its true flat shape + per-component bounds. This
        # supports factories whose per-element component count and bounds vary
        # (storage coeffs, LID sizing+type, RDII R+IA), not just uniform Box.
        factory = spec.construct()
        space = factory.space
        lo = np.asarray(space.low, dtype=float).ravel()
        hi = np.asarray(space.high, dtype=float).ravel()
        size = int(lo.shape[0])
        params = spec.params
        ids = params.get("link_ids") or params.get("node_ids") or params.get("subcatch_ids") or []
        if len(ids) == size:
            labels = tuple(f"{spec.kind}:{eid}" for eid in ids)
        else:
            labels = tuple(f"{spec.kind}[{i}]" for i in range(size))
        dims.append(
            DesignDimension(
                key=params.get("name", spec.kind),
                labels=labels,
                low=tuple(lo.tolist()),
                high=tuple(hi.tolist()),
            )
        )
    return dims


def _term_directions(env_config: EnvConfig) -> dict[str, str]:
    """Map reward-term name to C{"minimize"}/C{"maximize"} direction.

    Constructs the configured terms (lazy gym import) to read their
    public C{direction}/C{name}; an empty config means the env default,
    a single all-nodes C{flooding_volume} cost.

    @param env_config: The env config.
    @type env_config: L{EnvConfig}
    @return: Ordered name -> direction mapping.
    @rtype: dict
    """
    if not env_config.reward_terms:
        return {"flooding_volume": "minimize"}
    directions: dict[str, str] = {}
    for spec in env_config.reward_terms:
        term = spec.construct()
        directions[term.name] = term.direction
    return directions


# ---------------------------------------------------------------------------
# Job record
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """One optimization job and its mutable progress/result state.

    @ivar job_id: Unique identifier returned by C{gym_start_optimization}.
    @ivar env_config: The environment config being searched.
    @ivar opt_config: The optimization settings.
    @ivar output_dir: User-visible artifact directory.
    @ivar state: C{pending|running|done|failed|cancelled}.
    @ivar evaluations_done: Progress counter (monotone).
    @ivar error: Failure message when C{state == "failed"}.
    @ivar result: Result payload when C{state == "done"}.
    """

    job_id: str
    env_config: EnvConfig
    opt_config: OptimizationConfig
    output_dir: Path
    state: JobState = "pending"
    evaluations_done: int = 0
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe progress snapshot.

        @rtype: dict
        """
        with self.lock:
            return {
                "job_id": self.job_id,
                "state": self.state,
                "algorithm": self.opt_config.algorithm,
                "budget": self.opt_config.budget,
                # Full applied settings (algorithm/budget/seed + algo-specific),
                # so a silent downgrade vs. the requested run is unmissable (#9).
                "optimization": self.opt_config.resolved(),
                "evaluations_done": self.evaluations_done,
                "output_dir": str(self.output_dir),
                "error": self.error,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


class _Cancelled(Exception):
    """Internal: raised between evaluations on cooperative cancel."""


# ---------------------------------------------------------------------------
# Evaluation engine
# ---------------------------------------------------------------------------


class _Evaluator:
    """Runs one episode per candidate design and logs objectives.

    Reuses a single env (one Solver handle) across all evaluations of a
    job. Objectives are direction-adjusted costs-to-minimize: a
    C{maximize} term's component is negated, so every algorithm can
    minimize uniformly.

    @ivar evaluations: JSON-safe log of every evaluation, in order.
    """

    def __init__(self, job: Job, dims: list[DesignDimension]) -> None:
        """
        @param job: The owning job (progress + cancellation).
        @type job: L{Job}
        @param dims: Design-space metadata.
        @type dims: list of L{DesignDimension}
        """
        self._job = job
        self._dims = dims
        self._env = build_env(job.env_config)
        self._is_cip = job.env_config.env_type == "cip"
        # market/schedule/control_curve are single-step envs whose action is
        # the raw policy vector (one step runs the whole closed-loop episode).
        self._is_policy_env = job.env_config.env_type in (
            "market",
            "schedule",
            "control_curve",
        )
        self._directions = _term_directions(job.env_config)
        self.objective_names = list(self._directions)
        self.evaluations: list[dict[str, Any]] = []
        self._eval_file = (job.output_dir / "evaluations.jsonl").open("w", encoding="utf-8")

    def close(self) -> None:
        """Close the env and the evaluation log."""
        self._env.close()
        self._eval_file.close()

    def _design_payload(self, vector: np.ndarray) -> dict[str, np.ndarray]:
        """Split a flat decision vector into per-factory arrays.

        @param vector: Flat vector spanning all dims, in order.
        @type vector: L{numpy.ndarray}
        @return: Mapping of factory key to its slice.
        @rtype: dict
        """
        payload: dict[str, np.ndarray] = {}
        offset = 0
        for dim in self._dims:
            payload[dim.key] = np.asarray(vector[offset : offset + dim.size], dtype=np.float32)
            offset += dim.size
        return payload

    def evaluate(self, vector: np.ndarray) -> list[float]:
        """Evaluate one flat design vector with a full episode.

        @param vector: Flat decision vector.
        @type vector: L{numpy.ndarray}
        @return: Direction-adjusted cost per objective (minimize).
        @rtype: list of float
        @raise _Cancelled: When the job's cancel event is set.
        """
        if self._job.cancel_event.is_set():
            raise _Cancelled()
        design = self._design_payload(vector)

        if self._is_policy_env:
            # market/schedule: the action *is* the raw policy vector; one step
            # runs the whole episode under the controller.
            self._env.reset(seed=self._job.opt_config.seed)
            _obs, _reward, terminated, truncated, info = self._env.step(
                np.asarray(vector, dtype=np.float32)
            )
            components = dict(info.get("reward_components", {}))
            steps = 1
        elif self._is_cip:
            self._env.reset(seed=self._job.opt_config.seed)
            action = build_action(self._env.action_space, {"design": json_safe(design)})
            _obs, _reward, terminated, truncated, info = self._env.step(action)
            components = dict(info.get("reward_components", {}))
            steps = 1
        else:  # joint: design fixed at reset, neutral runtime each step
            self._env.reset(seed=self._job.opt_config.seed, options={"design_action": design})
            neutral = build_action(self._env.action_space, None)
            components: dict[str, float] = {}
            steps = 0
            terminated = truncated = False
            while steps < self._job.opt_config.max_steps_per_episode and not (
                terminated or truncated
            ):
                _obs, _reward, terminated, truncated, info = self._env.step(neutral)
                steps += 1
                for term, value in info.get("reward_components", {}).items():
                    components[term] = components.get(term, 0.0) + float(value)

        costs = [
            float(components.get(name, 0.0))
            * (1.0 if self._directions[name] == "minimize" else -1.0)
            for name in self.objective_names
        ]
        record = {
            "evaluation": len(self.evaluations) + 1,
            "decisions": json_safe(design),
            "objectives": dict(zip(self.objective_names, costs)),
            "steps": steps,
        }
        self.evaluations.append(record)
        self._eval_file.write(json.dumps(record) + "\n")
        self._eval_file.flush()
        with self._job.lock:
            self._job.evaluations_done = len(self.evaluations)
        return costs


# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------


def _flat_bounds(dims: list[DesignDimension]) -> tuple[np.ndarray, np.ndarray]:
    """Return flat (low, high) bound vectors across all dims.

    @rtype: tuple of two L{numpy.ndarray}
    """
    low = np.concatenate([np.broadcast_to(np.asarray(d.low, dtype=float), (d.size,)) for d in dims])
    high = np.concatenate(
        [np.broadcast_to(np.asarray(d.high, dtype=float), (d.size,)) for d in dims]
    )
    return low, high


def _run_random_search(evaluator: _Evaluator, dims, opt: OptimizationConfig) -> None:
    """Uniform random sampling of the design space (internal)."""
    low, high = _flat_bounds(dims)
    rng = np.random.default_rng(opt.seed)
    for _ in range(opt.budget):
        evaluator.evaluate(rng.uniform(low, high))


def _run_grid_search(evaluator: _Evaluator, dims, opt: OptimizationConfig) -> None:
    """Full factorial over per-dimension levels, capped at budget (internal)."""
    low, high = _flat_bounds(dims)
    axes = [np.linspace(lo, hi, opt.grid_levels) for lo, hi in zip(low, high)]
    for i, point in enumerate(itertools.product(*axes)):
        if i >= opt.budget:
            break
        evaluator.evaluate(np.asarray(point))


def _run_platypus(evaluator: _Evaluator, dims, opt: OptimizationConfig) -> None:
    """Run a Platypus MOEA named by ``opt.algorithm`` (internal).

    @raise ToolError: C{DEPENDENCY_MISSING} when platypus-opt is not
        installed.
    """
    try:
        import platypus
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.DEPENDENCY_MISSING}] Algorithm '{opt.algorithm}' "
            "requires the optional platypus-opt package. Install it with: "
            "pip install 'openswmm.gymnasium[platypus]'"
        ) from exc

    low, high = _flat_bounds(dims)
    n_vars = low.shape[0]
    n_objs = len(evaluator.objective_names)

    problem = platypus.Problem(n_vars, n_objs)
    problem.types[:] = [platypus.Real(lo, hi) for lo, hi in zip(low, high)]
    problem.directions[:] = [platypus.Problem.MINIMIZE] * n_objs
    problem.function = lambda variables: evaluator.evaluate(np.asarray(variables))

    algorithm_cls = getattr(platypus, _PLATYPUS_ALGORITHMS[opt.algorithm])
    algorithm = algorithm_cls(problem, population_size=opt.population_size)
    algorithm.run(opt.budget)


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------


def _assemble_result(evaluator: _Evaluator) -> dict[str, Any]:
    """Compute the Pareto set / best pick over all logged evaluations.

    Uses C{openswmm_gymnasium.scoring.pareto_front} for multi-objective
    runs; single-objective runs report the argmin.

    @param evaluator: The finished evaluator.
    @type evaluator: L{_Evaluator}
    @return: Result payload (objective names, evaluation count, pareto
        entries, best entry).
    @rtype: dict
    """
    evaluations = evaluator.evaluations
    names = evaluator.objective_names
    if not evaluations:
        return {"objective_names": names, "evaluations_count": 0, "pareto": [], "best": None}

    costs = np.array([[e["objectives"][n] for n in names] for e in evaluations])
    if len(names) == 1:
        best_idx = int(np.argmin(costs[:, 0]))
        pareto_idxs = [best_idx]
    else:
        from openswmm_gymnasium.scoring import pareto_front

        front = pareto_front(costs)
        pareto_idxs = [i for i, row in enumerate(costs) if any(np.allclose(row, f) for f in front)]
        # Best by equal-weight sum as a convenient single pick.
        best_idx = int(np.argmin(costs.sum(axis=1)))

    return {
        "objective_names": names,
        "evaluations_count": len(evaluations),
        "pareto": [evaluations[i] for i in pareto_idxs],
        "best": evaluations[best_idx],
    }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class JobManager:
    """Owns the background thread pool and all job records.

    @ivar max_workers: Pool size (plan §7.3 default: 2).
    """

    def __init__(self, max_workers: int = 2) -> None:
        """
        @param max_workers: Concurrent job cap; each job holds one
            Solver handle and saturates a core.
        @type max_workers: int
        """
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gym-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def start(
        self,
        env_config: EnvConfig,
        opt_config: OptimizationConfig,
        output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Validate, register, and submit a job; return its snapshot.

        @param env_config: Env config to search (must have design factories).
        @type env_config: L{EnvConfig}
        @param opt_config: Optimization settings.
        @type opt_config: L{OptimizationConfig}
        @param output_dir: Artifact directory; defaults to
            C{<inp_dir>/gym_runs/<job_id>} (plan §3.4).
        @type output_dir: str, L{Path}, or C{None}
        @return: Initial snapshot (C{state == "pending"}).
        @rtype: dict
        @raise ToolError: C{VALIDATION_ERROR} on unknown algorithms or
            configs without design factories.
        """
        algo = opt_config.algorithm
        if algo not in ("random_search", "grid_search") and algo not in _PLATYPUS_ALGORITHMS:
            valid = ["random_search", "grid_search", *_PLATYPUS_ALGORITHMS]
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown algorithm '{algo}'. "
                f"Valid algorithms: {', '.join(valid)}."
            )
        design_dimensions(env_config)  # validates design factories exist

        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        resolved = (
            Path(output_dir)
            if output_dir is not None
            else Path(env_config.inp_path).resolve().parent / "gym_runs" / job_id
        )
        resolved.mkdir(parents=True, exist_ok=True)
        job = Job(job_id=job_id, env_config=env_config, opt_config=opt_config, output_dir=resolved)

        # Persist the full job definition for review (plan §7.1 / §4.1).
        (resolved / "job.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "opt_config": opt_config.model_dump(mode="json"),
                    "env_config": env_config.model_dump(mode="json"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job)
        return job.snapshot()

    def _run(self, job: Job) -> None:
        """Execute *job* on a pool thread (internal)."""
        with job.lock:
            if job.cancel_event.is_set():  # cancelled while pending
                job.state = "cancelled"
                job.finished_at = time.time()
                return
            job.state = "running"
            job.started_at = time.time()

        evaluator: _Evaluator | None = None
        try:
            dims = design_dimensions(job.env_config)
            evaluator = _Evaluator(job, dims)
            if job.opt_config.algorithm == "random_search":
                _run_random_search(evaluator, dims, job.opt_config)
            elif job.opt_config.algorithm == "grid_search":
                _run_grid_search(evaluator, dims, job.opt_config)
            else:
                _run_platypus(evaluator, dims, job.opt_config)
            result = _assemble_result(evaluator)
            # Echo the applied search settings into the result so result.json
            # and gym_get_job_results both record what actually ran (#9).
            result["optimization"] = job.opt_config.resolved()
            (job.output_dir / "result.json").write_text(
                json.dumps(json_safe(result), indent=2) + "\n", encoding="utf-8"
            )
            with job.lock:
                job.result = json_safe(result)
                job.state = "done"
        except _Cancelled:
            with job.lock:
                job.state = "cancelled"
        except Exception as exc:  # job thread must never die silently
            with job.lock:
                job.state = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
        finally:
            with job.lock:
                job.finished_at = time.time()
            if evaluator is not None:
                try:
                    evaluator.close()
                except Exception:
                    pass

    # -- queries ---------------------------------------------------------------

    def _get_job(self, job_id: str) -> Job:
        """Look up a job record (internal).

        @raise ToolError: C{ELEMENT_NOT_FOUND} for unknown IDs.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            with self._lock:
                known = ", ".join(sorted(self._jobs)) or "<none>"
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] No job '{job_id}'. Known jobs: {known}."
            )
        return job

    def get(self, job_id: str) -> dict[str, Any]:
        """Return the progress snapshot for *job_id*.

        @rtype: dict
        @raise ToolError: C{ELEMENT_NOT_FOUND}.
        """
        return self._get_job(job_id).snapshot()

    def list(self) -> list[dict[str, Any]]:
        """Return snapshots of all jobs, newest first.

        @rtype: list of dict
        """
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted((j.snapshot() for j in jobs), key=lambda s: s["created_at"], reverse=True)

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Request cooperative cancellation of *job_id*.

        Takes effect before the next evaluation begins.

        @rtype: dict
        @raise ToolError: C{ELEMENT_NOT_FOUND}.
        """
        job = self._get_job(job_id)
        job.cancel_event.set()
        return job.snapshot()

    def get_env_config(self, job_id: str) -> EnvConfig:
        """Return the env config a job was started with.

        Used by C{gym_apply_design} to map decision vectors back onto
        model elements.

        @param job_id: The job to look up.
        @type job_id: str
        @return: The job's environment config.
        @rtype: L{EnvConfig}
        @raise ToolError: C{ELEMENT_NOT_FOUND}.
        """
        return self._get_job(job_id).env_config

    def results(self, job_id: str) -> dict[str, Any]:
        """Return the full result payload of a finished job.

        @rtype: dict
        @raise ToolError: C{ELEMENT_NOT_FOUND} for unknown jobs;
            C{INVALID_STATE} when the job has not finished successfully.
        """
        job = self._get_job(job_id)
        snap = job.snapshot()
        if snap["state"] != "done":
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Job '{job_id}' is "
                f"'{snap['state']}'; results are available when state is "
                f"'done'. {snap.get('error') or ''}".strip()
            )
        with job.lock:
            result = dict(job.result or {})
        result.update(
            {
                "job_id": job_id,
                "output_dir": snap["output_dir"],
                "artifacts": [
                    str(job.output_dir / name)
                    for name in ("job.json", "evaluations.jsonl", "result.json")
                ],
            }
        )
        return result

    def shutdown(self) -> None:
        """Cancel all jobs and stop the pool (server shutdown)."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
