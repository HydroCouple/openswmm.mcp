"""Gym tools: capability discovery and environment-config management.

Phase 2 of C{docs/developer/GYMNASIUM_INTEGRATION_PLAN.md}: the tools an
LLM uses to discover the C{openswmm.gymnasium} vocabulary
(L{list_capabilities}), compose and persist declarative
L{EnvConfig<openswmm_mcp.gym_support.config.EnvConfig>}s (CRUD tools),
sanity-check them against the real engine (L{validate_env_config}),
and browse the registered benchmark scenarios (L{describe_benchmark}).

Configs are persisted as JSON by
L{GymStore<openswmm_mcp.gym_support.store.GymStore>} under
C{<working_dir>/gym_configs} by default; every tool accepts a
C{config_dir} override (plan §7.1).

C{openswmm_gymnasium} is imported lazily — only
L{validate_env_config} and L{describe_benchmark} need it installed.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: MIT
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP
from pydantic import ValidationError

from openswmm_mcp.dependencies import get_settings, require_gymnasium
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.gym_support import registry
from openswmm_mcp.gym_support.config import EnvConfig, build_env
from openswmm_mcp.gym_support.store import GymStore

gym_mcp = FastMCP("gym")

#: Default sub-directory (under the server working dir) for config JSON.
_CONFIG_SUBDIR = "gym_configs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_store(ctx: Context, config_dir: str | None) -> GymStore:
    """Return a L{GymStore} for *config_dir* or the server default.

    @param ctx: FastMCP request context.
    @type ctx: L{Context}
    @param config_dir: Explicit store directory, or C{None} for
        C{<working_dir>/gym_configs}.
    @type config_dir: str or C{None}
    @return: The store.
    @rtype: L{GymStore}
    """
    if config_dir is not None:
        return GymStore(config_dir)
    settings = get_settings(ctx)
    return GymStore(Path(settings.working_dir) / _CONFIG_SUBDIR)


def _parse_config(config: dict[str, Any]) -> EnvConfig:
    """Validate a raw config dict into an L{EnvConfig}.

    @param config: Raw JSON config from the client.
    @type config: dict
    @return: The validated config.
    @rtype: L{EnvConfig}
    @raise ToolError: C{VALIDATION_ERROR} with the Pydantic detail when
        the config does not satisfy the schema.
    """
    try:
        return EnvConfig(**config)
    except ToolError:
        raise
    except (ValidationError, TypeError) as exc:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid environment config: {exc}. "
            "Call gym_list_capabilities for valid kinds and param schemas."
        ) from exc


def _describe_space(space: Any) -> dict[str, Any]:
    """Recursively summarise a Gymnasium space as plain JSON.

    Box leaves report shape and scalar low/high bounds; Dict spaces
    recurse per key; anything else falls back to C{repr}.

    @param space: A C{gymnasium.spaces.Space}.
    @type space: C{gymnasium.spaces.Space}
    @return: JSON-serializable description.
    @rtype: dict
    """
    type_name = type(space).__name__
    if type_name == "Dict":
        return {
            "type": "Dict",
            "spaces": {key: _describe_space(sub) for key, sub in space.spaces.items()},
        }
    if type_name == "Box":
        return {
            "type": "Box",
            "shape": list(space.shape),
            "low": float(space.low.min()),
            "high": float(space.high.max()),
        }
    return {"type": type_name, "repr": repr(space)}


def _summarize(config: EnvConfig) -> dict[str, Any]:
    """Return a compact JSON summary of *config* for tool responses.

    @param config: The config to summarise.
    @type config: L{EnvConfig}
    @return: Summary dict (env type, inp path, kind names).
    @rtype: dict
    """
    return {
        "env_type": config.env_type,
        "inp_path": config.inp_path,
        "runtime_factories": [s.kind for s in config.runtime_factories],
        "design_factories": [s.kind for s in config.design_factories],
        "reward_terms": [s.kind for s in config.reward_terms],
        "wrappers": [s.kind for s in config.wrappers],
        "control_interval_steps": config.control_interval_steps,
        "max_episode_steps": config.max_episode_steps,
    }


# ---------------------------------------------------------------------------
# Capability discovery
# ---------------------------------------------------------------------------


@gym_mcp.tool()
async def list_capabilities(ctx: Context) -> dict:
    """List the full declarative vocabulary for environment configs.

    Returns every registered kind — reward terms, runtime/design action
    factories, and wrappers — with a one-line description and the JSON
    schema of its C{params}, plus the valid C{env_type} values. Derived
    from the kind registry, so it is always in sync with what
    C{gym_create_env_config} accepts.

    Works without the gym extra installed (pure metadata).
    """
    capabilities: dict[str, list[dict[str, Any]]] = {}
    for spec in registry.list_kinds():
        capabilities.setdefault(spec.category, []).append(
            {
                "kind": spec.kind,
                "description": spec.description,
                "params_schema": spec.params_model.model_json_schema(),
            }
        )
    return {
        "env_types": {
            "rtc": "Runtime control only (SwmmRTCEnv).",
            "cip": "Design-only capital improvement (SwmmCIPEnv); one episode "
            "evaluates one design.",
            "joint": "Combined design + runtime control (SwmmJointCIPRTCEnv).",
            "mo_rtc": "Multi-objective runtime control (SwmmMORTCEnv); requires "
            "ideal_point and reference_point.",
            "market": "Tune a reactive market controller's cost-curve + PID "
            "params (SwmmControlEnv); requires market_config.",
            "schedule": "Open-loop full-event optimal control (SwmmControlEnv); "
            "requires structure_ids, n_points, control_interval_seconds.",
            "control_curve": "Tune a reactive piecewise-linear control policy "
            "(SwmmControlEnv); requires a policy_factory and "
            "control_interval_seconds. The decision vector is the per-knot "
            "breakpoint settings, optimized to trace the cost curve (e.g. CSO "
            "vs flooding volume).",
        },
        "capabilities": capabilities,
        "notes": [
            "Compose configs with gym_create_env_config; observation features "
            "are listed per element ID in 'observations'.",
            "reward_terms left empty means the env default: a single "
            "all-nodes flooding_volume term.",
            "policy_factory 'control_curve' is searched by gym_start_optimization "
            "just like design_factories; decode a result vector to per-asset "
            "curves with gym_decode_policy.",
        ],
    }


@gym_mcp.tool()
async def describe_benchmark(ctx: Context, benchmark_id: str | None = None) -> dict:
    """List or describe the registered C{OpenSWMM/*} benchmark env IDs.

    Without I{benchmark_id}, returns all Gymnasium registry entries whose
    ID starts with C{"OpenSWMM/"}. With it, returns that entry's details.
    Requires the gym extra.
    """
    require_gymnasium("gym_describe_benchmark")
    import gymnasium
    import openswmm_gymnasium  # noqa: F401  (side effect: registers env IDs)

    entries = {
        env_id: spec
        for env_id, spec in gymnasium.registry.items()
        if env_id.startswith("OpenSWMM/")
    }
    if benchmark_id is not None:
        spec = entries.get(benchmark_id)
        if spec is None:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] Unknown benchmark "
                f"'{benchmark_id}'. Available: {sorted(entries)}."
            )
        return {
            "id": benchmark_id,
            "entry_point": str(spec.entry_point),
            "kwargs": sorted(spec.kwargs) if spec.kwargs else [],
        }
    return {
        "benchmarks": [
            {"id": env_id, "entry_point": str(spec.entry_point)}
            for env_id, spec in sorted(entries.items())
        ]
    }


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


@gym_mcp.tool()
async def create_env_config(
    ctx: Context,
    name: str,
    config: dict,
    overwrite: bool = False,
    config_dir: str | None = None,
) -> dict:
    """Validate and persist a named environment config as JSON.

    I{config} must satisfy the EnvConfig schema (see
    C{gym_list_capabilities} for kinds, params, and env types). The
    config is written to C{<config_dir>/<name>.json} — user-visible,
    reviewable, and versionable — and survives server restarts.
    """
    env_config = _parse_config(config)
    store = _get_store(ctx, config_dir)
    path = await asyncio.to_thread(store.save_config, name, env_config, overwrite=overwrite)
    return {"name": name, "path": str(path), "summary": _summarize(env_config)}


@gym_mcp.tool()
async def get_env_config(ctx: Context, name: str, config_dir: str | None = None) -> dict:
    """Return the full stored config JSON for I{name}."""
    store = _get_store(ctx, config_dir)
    env_config = await asyncio.to_thread(store.get_config, name)
    return {
        "name": name,
        "path": str(store.config_dir / f"{name}.json"),
        "config": env_config.model_dump(mode="json"),
    }


@gym_mcp.tool()
async def list_env_configs(ctx: Context, config_dir: str | None = None) -> dict:
    """List stored config names in the config directory."""
    store = _get_store(ctx, config_dir)
    names = await asyncio.to_thread(store.list_configs)
    return {"config_dir": str(store.config_dir), "count": len(names), "names": names}


@gym_mcp.tool()
async def delete_env_config(ctx: Context, name: str, config_dir: str | None = None) -> dict:
    """Delete the named stored config."""
    store = _get_store(ctx, config_dir)
    path = await asyncio.to_thread(store.delete_config, name)
    return {"name": name, "deleted": str(path)}


# ---------------------------------------------------------------------------
# Validation against the real engine
# ---------------------------------------------------------------------------


@gym_mcp.tool()
async def validate_env_config(
    ctx: Context,
    name: str | None = None,
    config: dict | None = None,
    config_dir: str | None = None,
) -> dict:
    """Instantiate the config against the real engine and report spaces.

    Pass either a stored config I{name} or an inline I{config} dict
    (exactly one). The env is constructed, reset once so every element
    ID is resolved against the model, then closed — catching bad IDs and
    inconsistent specs before any long run. Requires the gym extra.

    Returns the resolved observation size, the full (wrapped) action
    space, and the reward-term wiring.
    """
    require_gymnasium("gym_validate_env_config")
    if (name is None) == (config is None):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Pass exactly one of 'name' "
            "(a stored config) or 'config' (an inline config dict)."
        )
    if name is not None:
        store = _get_store(ctx, config_dir)
        env_config = await asyncio.to_thread(store.get_config, name)
    else:
        env_config = _parse_config(config)

    def _instantiate() -> dict[str, Any]:
        env = build_env(env_config)
        try:
            try:
                obs, _info = env.reset()
            except Exception as exc:
                raise ToolError(
                    f"[{ErrorCode.ENGINE_ERROR}] Env constructed but reset "
                    f"failed (often an unknown element ID in observations, "
                    f"factories, or reward terms): {exc}"
                ) from exc
            return {
                "observation_size": int(obs.shape[0]),
                "observation_space": _describe_space(env.observation_space),
                "action_space": _describe_space(env.action_space),
                "wrappers_applied": [s.kind for s in env_config.wrappers],
            }
        finally:
            env.close()

    result = await asyncio.to_thread(_instantiate)
    result["valid"] = True
    result["summary"] = _summarize(env_config)
    if name is not None:
        result["name"] = name
    return result
