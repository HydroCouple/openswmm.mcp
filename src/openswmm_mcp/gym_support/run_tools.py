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

"""Gym actions: episode rollouts, the interactive env loop and optimisation jobs.

Implementations behind the C{gym_env} and C{gym_job} tools
(L{openswmm_mcp.tools.gym}).

Two execution styles:

  - L{run_episode} — one blocking rollout of a stored/inline config
    under a declarative policy (C{constant} / C{random} / C{replay}),
    artifacts written under a user-visible run directory.
  - L{env_open} / L{env_reset} / L{env_step} / L{env_close} /
    L{list_envs} — a live env held server-side so the LLM itself acts
    as the control policy, one C{gym_env(action='step')} per control interval.

All engine work runs in worker threads; per-env locks serialise
concurrent access to the same C{env_id}.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: Apache-2.0
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastmcp import Context
from openswmm_gymnasium.spec.config import EnvConfig, JsonObject, coerce_json_param
from openswmm_gymnasium.spec.envs import build_action, json_safe
from openswmm_gymnasium.spec.envs import run_episode as _run_episode_sync
from pydantic import ValidationError

from openswmm_mcp.dependencies import get_env_manager, get_job_manager, require_gymnasium
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.gym_support import tool_errors
from openswmm_mcp.gym_support.config_tools import _get_store, _parse_config, _summarize
from openswmm_mcp.gym_support.jobs import OptimizationConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_config(
    ctx: Context,
    name: str | None,
    config: dict | None,
    config_dir: str | None,
) -> EnvConfig:
    """Resolve exactly one of stored *name* / inline *config*.

    @raise ToolError: C{VALIDATION_ERROR} unless exactly one is given.
    @rtype: L{EnvConfig}
    """
    if (name is None) == (config is None):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Pass exactly one of 'name' "
            "(a stored config) or 'config' (an inline config dict)."
        )
    if name is not None:
        store = _get_store(ctx, config_dir)
        return await asyncio.to_thread(store.get_config, name)
    return _parse_config(config)


def _default_run_dir(env_config: EnvConfig, run_id: str) -> Path:
    """Return the default run directory beside the model (plan §3.4).

    @param env_config: Config whose C{inp_path} anchors the directory.
    @type env_config: L{EnvConfig}
    @param run_id: Unique run identifier.
    @type run_id: str
    @return: C{<inp_dir>/gym_runs/<run_id>}.
    @rtype: L{Path}
    """
    return Path(env_config.inp_path).resolve().parent / "gym_runs" / run_id


# ---------------------------------------------------------------------------
# Blocking episode rollout
# ---------------------------------------------------------------------------


@tool_errors
async def run_episode(
    ctx: Context,
    name: str | None = None,
    config: JsonObject = None,
    policy: JsonObject = None,
    run_dir: str | None = None,
    max_steps: int = 10_000,
    seed: int | None = None,
    config_dir: str | None = None,
) -> dict:
    """Run one episode of a stored or inline env config and return totals.

    Pass either a stored config I{name} or an inline I{config} (exactly
    one). I{policy} selects the per-step action source:

      - C{{"kind": "constant", "action": {"runtime": {...}}}} — fixed
        action every step; omitted leaves default to the Box midpoint
        (so C{policy=None} runs a neutral do-nothing baseline).
      - C{{"kind": "random", "seed": 7}} — uniform samples from the
        action space.
      - C{{"kind": "replay", "actions": [{...}, ...]}} — explicit
        per-step sequence; the episode stops when it is exhausted.

    Artifacts (C{trajectory.jsonl}, C{summary.json}) are written to
    I{run_dir}, defaulting to C{<inp_dir>/gym_runs/<run_id>/} so they
    are always user-reviewable. Requires the gym extra.
    """
    require_gymnasium("gym_env(action='run_episode')")
    policy = coerce_json_param(policy, "policy")
    env_config = await _resolve_config(ctx, name, config, config_dir)
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{seed if seed is not None else 'x'}"
    resolved_run_dir = (
        Path(run_dir) if run_dir is not None else _default_run_dir(env_config, run_id)
    )
    summary = await asyncio.to_thread(
        _run_episode_sync,
        env_config,
        policy or {"kind": "constant"},
        resolved_run_dir,
        max_steps=max_steps,
        seed=seed,
    )
    summary["run_id"] = run_id
    summary["run_dir"] = str(resolved_run_dir)
    summary["config_summary"] = _summarize(env_config)
    if name is not None:
        summary["name"] = name
    return summary


# ---------------------------------------------------------------------------
# Interactive LLM-as-controller loop
# ---------------------------------------------------------------------------


@tool_errors
async def env_open(
    ctx: Context,
    env_id: str = "default",
    name: str | None = None,
    config: JsonObject = None,
    config_dir: str | None = None,
) -> dict:
    """Open an interactive env the LLM can drive step by step.

    Builds the env from a stored config I{name} or inline I{config}
    (exactly one) and holds it server-side under I{env_id}. Call
    C{gym_env(action='reset')} to start an episode, C{gym_env(action='step')} repeatedly to
    control it, and C{gym_env(action='close')} when done — open envs hold engine
    handles and are swept after prolonged idleness. Requires the gym
    extra.
    """
    require_gymnasium("gym_env(action='open')")
    env_config = await _resolve_config(ctx, name, config, config_dir)
    manager = get_env_manager(ctx)
    handle = await asyncio.to_thread(manager.open, env_id, env_config)
    from openswmm_mcp.gym_support.config_tools import _describe_space

    return {
        "env_id": env_id,
        "summary": _summarize(env_config),
        "action_space": _describe_space(handle.env.action_space),
        "observation_space": _describe_space(handle.env.observation_space),
        "next": "Call gym_env(action='reset') to start an episode.",
    }


@tool_errors
async def env_reset(ctx: Context, env_id: str = "default", seed: int | None = None) -> dict:
    """Reset the interactive env and return the initial observation."""
    manager = get_env_manager(ctx)
    handle = manager.get(env_id)

    def _reset() -> dict:
        with handle.lock:
            obs, info = handle.env.reset(seed=seed)
            handle.step_count = 0
            handle.touch()
            return {
                "env_id": env_id,
                "observation": json_safe(obs),
                "info": json_safe(info),
            }

    return await asyncio.to_thread(_reset)


@tool_errors
async def env_step(
    ctx: Context,
    env_id: str = "default",
    action: JsonObject = None,
) -> dict:
    """Advance the interactive env one control interval.

    I{action} mirrors the env's Dict action space, e.g.
    C{{"runtime": {"orifice_setting": [0.4]}}}. Omitted leaves default
    to the Box midpoint; values are clipped to the leaf bounds. Returns
    the observation, reward (scalar, or vector for C{mo_rtc}),
    termination flags, and the per-term reward components from C{info}.
    """
    action = coerce_json_param(action, "action")
    manager = get_env_manager(ctx)
    handle = manager.get(env_id)

    def _step() -> dict:
        with handle.lock:
            env_action = build_action(handle.env.action_space, action)
            obs, reward, terminated, truncated, info = handle.env.step(env_action)
            handle.step_count += 1
            handle.touch()
            return {
                "env_id": env_id,
                "step": handle.step_count,
                "observation": json_safe(obs),
                "reward": json_safe(reward),
                "terminated": bool(terminated),
                "truncated": bool(truncated),
                "info": json_safe(info),
            }

    return await asyncio.to_thread(_step)


@tool_errors
async def env_close(ctx: Context, env_id: str = "default") -> dict:
    """Close the interactive env and release its engine handle."""
    manager = get_env_manager(ctx)
    await asyncio.to_thread(manager.close, env_id)
    return {"env_id": env_id, "closed": True}


@tool_errors
async def list_envs(ctx: Context) -> dict:
    """List open interactive envs with their idle times and step counts."""
    manager = get_env_manager(ctx)
    envs = manager.list()
    return {"count": len(envs), "envs": envs}


# ---------------------------------------------------------------------------
# Background optimization jobs (plan Phase 4)
# ---------------------------------------------------------------------------


@tool_errors
async def start_optimization(
    ctx: Context,
    name: str | None = None,
    config: JsonObject = None,
    optimization: JsonObject = None,
    output_dir: str | None = None,
    config_dir: str | None = None,
) -> dict:
    """Start a background design-search job; returns immediately.

    The env config (stored I{name} or inline I{config}, exactly one)
    must have a searchable static factory: design_factories (env_type
    C{cip} or C{joint}) or a policy_factory (env_type C{control_curve},
    C{market}, C{schedule}). For C{control_curve} the decision vector is
    the per-knot breakpoint settings; decode a result with
    C{gym_job(action='decode_policy')}.
    I{optimization} sets the run, e.g.::

        {"algorithm": "random_search", "budget": 40, "seed": 7}
        {"algorithm": "nsga2", "budget": 400, "population_size": 20}

    Algorithms: C{random_search}, C{grid_search} (grid_levels per
    dimension), or a Platypus MOEA — C{nsga2}, C{nsga3}, C{spea2},
    C{moead}, C{gde3} (requires platypus-opt). Each evaluation is one
    episode; objectives are the direction-adjusted reward-term totals
    (costs to minimize). Artifacts (C{job.json}, C{evaluations.jsonl},
    C{result.json}) land in I{output_dir}, defaulting to
    C{<inp_dir>/gym_runs/<job_id>/}.

    Poll with C{gym_job(action='status')}; fetch results with C{gym_job(action='results')};
    stop with C{gym_job(action='cancel')}. Requires the gym extra.
    """
    require_gymnasium("gym_job(action='start')")
    optimization = coerce_json_param(optimization, "optimization")
    env_config = await _resolve_config(ctx, name, config, config_dir)
    try:
        opt_config = OptimizationConfig(**(optimization or {}))
    except ValidationError as exc:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid optimization settings: {exc}"
        ) from exc
    manager = get_job_manager(ctx)
    snapshot = await asyncio.to_thread(manager.start, env_config, opt_config, output_dir)
    if name is not None:
        snapshot["name"] = name
    return snapshot


@tool_errors
async def get_job(ctx: Context, job_id: str) -> dict:
    """Return the progress snapshot of an optimization job."""
    return get_job_manager(ctx).get(job_id)


@tool_errors
async def list_jobs(ctx: Context) -> dict:
    """List all optimization jobs (newest first) with their states."""
    jobs = get_job_manager(ctx).list()
    return {"count": len(jobs), "jobs": jobs}


@tool_errors
async def cancel_job(ctx: Context, job_id: str) -> dict:
    """Request cooperative cancellation; takes effect between evaluations."""
    return get_job_manager(ctx).cancel(job_id)


@tool_errors
async def get_job_results(ctx: Context, job_id: str) -> dict:
    """Return the results of a finished job.

    Includes every evaluation's labeled decision vector and objective
    costs, the Pareto-optimal subset (multi-objective), a convenient
    single best pick, and artifact paths.
    """
    return get_job_manager(ctx).results(job_id)


def _decisions_to_vector(decisions: dict, labels: list[str]) -> list[float]:
    """Flatten a logged C{decisions} payload back to a vector in *labels* order.

    Each control-curve dimension is a single-component group keyed by its
    label, so C{decisions[label]} is a one-element list.

    @raise ToolError: C{VALIDATION_ERROR} if a label is missing.
    """
    vector: list[float] = []
    for label in labels:
        comp = decisions.get(label)
        if comp is None:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Decision component '{label}' "
                "missing from the logged evaluation; was this a control_curve job?"
            )
        vector.append(float(comp[0] if isinstance(comp, (list, tuple)) else comp))
    return vector


@tool_errors
async def decode_policy(ctx: Context, job_id: str, index: str = "best") -> dict:
    """Decode a control-curve result vector into per-asset PWL curves.

    For a finished C{control_curve} optimization job, maps a chosen decision
    vector back onto each controlled link's human-readable curve — the applied
    (monotonic-projected) C{y_values} at each fixed C{x_knot}, with the
    observed node and attribute. I{index} selects which point: C{"best"} (the
    convenience single pick) or an integer string indexing the Pareto front
    (C{"0"}, C{"1"}, ...). Requires the gym extra.
    """
    require_gymnasium("gym_job(action='decode_policy')")
    from openswmm_gymnasium.spaces import ControlCurvePolicySpace

    manager = get_job_manager(ctx)
    env_config = manager.get_env_config(job_id)
    if env_config.env_type != "control_curve" or env_config.policy_factory is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] gym_job(action='decode_policy') applies to "
            f"'control_curve' jobs; job '{job_id}' is '{env_config.env_type}'."
        )
    results = manager.results(job_id)  # raises unless state == "done"

    if index == "best":
        evaluation = results.get("best")
    else:
        try:
            i = int(index)
        except ValueError as exc:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] index must be 'best' or an "
                f"integer Pareto-front position, got {index!r}."
            ) from exc
        front = results.get("pareto") or []
        if not (0 <= i < len(front)):
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Pareto index {i} out of range "
                f"(front has {len(front)} entries)."
            )
        evaluation = front[i]
    if not evaluation:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] No evaluation to decode for job '{job_id}'."
        )

    space = ControlCurvePolicySpace.from_params(env_config.policy_factory.params)
    vector = _decisions_to_vector(evaluation["decisions"], space.labels)
    curves = space.decode_curves(vector)
    return {
        "job_id": job_id,
        "index": index,
        "objectives": evaluation.get("objectives", {}),
        "x_normalized": space.x_normalized,
        "rate_limit": space.rate_limit,
        "control_interval_steps": space.control_interval_steps,
        "curves": curves,
    }
