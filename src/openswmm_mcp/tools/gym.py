"""Gym tool set (``OPENSWMM_MCP_TOOLSETS=core,gym``): five tools over openswmm.gymnasium.

Each tool dispatches an ``action`` to the implementations in
``openswmm_mcp.gym_support`` (config/run/score actions), so the whole gym surface
costs five tool definitions.
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import Context

from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.gym_support import config_tools, run_tools, score_tools
from openswmm_mcp.gym_support.config import (
    JsonFloatMatrix,
    JsonFloatVector,
    JsonObject,
    JsonStrList,
)


def _need(value: Any, name: str, action: str) -> Any:
    if value in (None, "", []):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] action '{action}' needs '{name}'.")
    return value


async def gym_describe(ctx: Context, topic: str = "") -> dict:
    """List gym building blocks: env types, observation features (including any engine
    field via observations.fields), reward terms, design factories, runtime actuators,
    policy spaces and worked config examples. "benchmark" lists benchmark scenarios;
    "benchmark:<id>" describes one."""
    if topic.startswith("benchmark"):
        _, _, benchmark = topic.partition(":")
        return await config_tools.describe_benchmark(ctx, benchmark or None)
    return await config_tools.list_capabilities(ctx)


async def gym_config(
    ctx: Context,
    action: Literal["create", "get", "list", "validate", "delete"],
    name: str | None = None,
    spec: JsonObject = None,
    overwrite: bool = False,
) -> dict:
    """Create, read, list, validate or delete a named environment spec (JSON, see
    gym_describe). validate builds the env against the real engine."""
    if action == "list":
        return await config_tools.list_env_configs(ctx)
    if action == "validate":
        return await config_tools.validate_env_config(ctx, name=name, config=spec)
    name = _need(name, "name", action)
    if action == "create":
        return await config_tools.create_env_config(
            ctx, name, _need(spec, "spec", action), overwrite=overwrite
        )
    if action == "get":
        return await config_tools.get_env_config(ctx, name)
    return await config_tools.delete_env_config(ctx, name)


async def gym_env(
    ctx: Context,
    action: Literal["open", "reset", "step", "close", "list", "run_episode"],
    env_id: str = "default",
    config: str | None = None,
    spec: JsonObject = None,
    step_action: JsonObject = None,
    policy: JsonObject = None,
    seed: int | None = None,
    max_steps: int = 10_000,
) -> dict:
    """Drive an environment: open it from a named config or inline spec, reset, step with
    step_action ({"design": {...}, "runtime": {...}}), close, list open envs, or
    run_episode (a full rollout under a policy: constant, random or replay)."""
    if action == "list":
        return await run_tools.list_envs(ctx)
    if action == "open":
        return await run_tools.env_open(ctx, env_id, name=config, config=spec)
    if action == "reset":
        return await run_tools.env_reset(ctx, env_id, seed=seed)
    if action == "step":
        return await run_tools.env_step(ctx, env_id, _need(step_action, "step_action", action))
    if action == "close":
        return await run_tools.env_close(ctx, env_id)
    return await run_tools.run_episode(
        ctx, name=config, config=spec, policy=policy, max_steps=max_steps, seed=seed
    )


async def gym_job(
    ctx: Context,
    action: Literal["start", "status", "results", "cancel", "list", "decode_policy"],
    job_id: str | None = None,
    config: str | None = None,
    spec: JsonObject = None,
    optimization: JsonObject = None,
    index: str = "best",
) -> dict:
    """Run optimisation jobs (e.g. NSGA-II design or control search) in the background:
    start from a named config or inline spec with optimization settings, then poll
    status, fetch results (Pareto front), cancel, list, or decode a policy."""
    if action == "list":
        return await run_tools.list_jobs(ctx)
    if action == "start":
        return await run_tools.start_optimization(
            ctx, name=config, config=spec, optimization=optimization
        )
    job_id = _need(job_id, "job_id", action)
    if action == "status":
        return await run_tools.get_job(ctx, job_id)
    if action == "results":
        return await run_tools.get_job_results(ctx, job_id)
    if action == "cancel":
        return await run_tools.cancel_job(ctx, job_id)
    return await run_tools.decode_policy(ctx, job_id, index)


async def gym_score(
    ctx: Context,
    action: Literal["pareto", "score", "compare", "apply_design"],
    job_id: str | None = None,
    job_ids: JsonStrList = None,
    front: JsonFloatMatrix = None,
    indicators: JsonStrList = None,
    reference_point: JsonFloatVector = None,
    ideal_point: JsonFloatVector = None,
    reference_front: JsonFloatMatrix = None,
    weights: JsonFloatMatrix = None,
    session_id: str = "default",
    evaluation: int | str = "best",
) -> dict:
    """Score optimisation results: Pareto-filter a front, compute indicators (hypervolume,
    IGD, epsilon, R2, spread), compare jobs, or apply a job's chosen design to a session."""
    indicator_args = {
        "reference_point": reference_point,
        "ideal_point": ideal_point,
        "reference_front": reference_front,
        "weights": weights,
    }
    if action == "pareto":
        return await score_tools.pareto_filter(ctx, front=front, job_id=job_id)
    if action == "score":
        return await score_tools.score_front(
            ctx,
            _need(indicators, "indicators", action),
            front=front,
            job_id=job_id,
            **indicator_args,
        )
    if action == "compare":
        return await score_tools.compare_runs(
            ctx,
            _need(job_ids, "job_ids", action),
            _need(indicators, "indicators", action),
            **indicator_args,
        )
    return await score_tools.apply_design(
        ctx, _need(job_id, "job_id", action), session_id=session_id, evaluation=evaluation
    )


TOOLS = (gym_describe, gym_config, gym_env, gym_job, gym_score)
