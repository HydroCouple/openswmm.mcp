"""Episode execution and live interactive-env management (plan Phase 3).

Two responsibilities behind the C{gym_run_episode} and C{gym_env_*}
tools:

  - L{run_episode} — synchronous single-rollout runner driving a
    config-built env with a declarative policy (C{constant} /
    C{random} / C{replay}), writing reviewable artifacts
    (C{trajectory.jsonl}, C{summary.json}) under a user-visible run
    directory (CLAUDE.md §4.1).
  - L{EnvManager} — registry of open interactive envs keyed by
    C{env_id} for the LLM-as-controller loop, mirroring the model
    C{SessionManager}: explicit close, capacity cap, and lazy
    idle-timeout sweeps.

Everything here is synchronous; tool handlers run it via
C{asyncio.to_thread}. C{openswmm_gymnasium} is only imported indirectly
through L{build_env<openswmm_mcp.gym_support.config.build_env>}.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: MIT
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.gym_support.config import EnvConfig, build_env

# ---------------------------------------------------------------------------
# JSON <-> action / observation conversion
# ---------------------------------------------------------------------------


def json_safe(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays to plain JSON types.

    @param value: Any value from an env step (obs, reward, info, ...).
    @type value: object
    @return: A JSON-serializable equivalent.
    @rtype: object
    """
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def _midpoint(space: Any) -> np.ndarray:
    """Return the elementwise midpoint of a Box space.

    @param space: A C{gymnasium.spaces.Box}.
    @type space: C{gymnasium.spaces.Box}
    @return: C{(low + high) / 2} in the space's dtype.
    @rtype: L{numpy.ndarray}
    """
    return ((space.low + space.high) / 2.0).astype(space.dtype)


def _fill_dict_action(space: Any, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Build an action for a Dict space from a (partial) JSON payload.

    Leaves named in *payload* are converted to arrays and clipped
    against their Box bounds; leaves missing from *payload* default to
    the Box midpoint. Unknown keys are rejected.

    @param space: The C{gymnasium.spaces.Dict} to fill.
    @type space: C{gymnasium.spaces.Dict}
    @param payload: JSON mapping of leaf name to list/scalar, or C{None}.
    @type payload: dict or C{None}
    @return: A fully-populated action for *space*.
    @rtype: dict
    @raise ToolError: C{VALIDATION_ERROR} on unknown keys or wrong shapes.
    """
    payload = payload or {}
    unknown = set(payload) - set(space.spaces)
    if unknown:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown action keys {sorted(unknown)}; "
            f"valid keys: {sorted(space.spaces)}."
        )
    action: dict[str, Any] = {}
    for key, sub in space.spaces.items():
        if type(sub).__name__ == "Dict":
            action[key] = _fill_dict_action(sub, payload.get(key))
            continue
        if key not in payload:
            action[key] = _midpoint(sub)
            continue
        arr = np.asarray(payload[key], dtype=sub.dtype).reshape(-1)
        if arr.shape != sub.shape:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Action '{key}' has shape "
                f"{list(arr.shape)}, expected {list(sub.shape)}."
            )
        action[key] = np.clip(arr, sub.low, sub.high)
    return action


def build_action(action_space: Any, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Convert a JSON action payload into a valid env action.

    The payload mirrors the env's Dict action space, e.g.
    C{{"runtime": {"orifice_setting": [0.5]}}}; missing leaves default
    to the Box midpoint, so C{None} yields a neutral action.

    @param action_space: The env's top-level Dict action space.
    @type action_space: C{gymnasium.spaces.Dict}
    @param payload: JSON action payload or C{None}.
    @type payload: dict or C{None}
    @return: Action dict with numpy leaves.
    @rtype: dict
    @raise ToolError: C{VALIDATION_ERROR} on malformed payloads.
    """
    if type(action_space).__name__ != "Dict":
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Expected a Dict action space, "
            f"got {type(action_space).__name__}. Wrapped (masked/rescaled) "
            "spaces are driven through their own structure."
        )
    return _fill_dict_action(action_space, payload)


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


class _Policy:
    """Declarative per-step action source for L{run_episode}.

    @ivar exhausted: Set by C{replay} when its action list ran out.
    """

    def __init__(self, action_space: Any, spec: dict[str, Any]) -> None:
        """
        @param action_space: The env's action space.
        @type action_space: C{gymnasium.spaces.Space}
        @param spec: Policy spec: C{{"kind": "constant", "action": {...}}},
            C{{"kind": "random", "seed": 7}}, or
            C{{"kind": "replay", "actions": [{...}, ...]}}.
        @type spec: dict
        @raise ToolError: C{VALIDATION_ERROR} on unknown kinds or
            missing replay actions.
        """
        self._space = action_space
        self._kind = spec.get("kind", "constant")
        self.exhausted = False
        if self._kind == "constant":
            self._constant = build_action(action_space, spec.get("action"))
        elif self._kind == "random":
            action_space.seed(spec.get("seed"))
        elif self._kind == "replay":
            actions = spec.get("actions")
            if not actions:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Replay policy requires a "
                    "non-empty 'actions' list."
                )
            self._replay = [build_action(action_space, a) for a in actions]
            self._cursor = 0
        else:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown policy kind "
                f"'{self._kind}'. Valid kinds: constant, random, replay."
            )

    def next_action(self) -> dict[str, Any] | None:
        """Return the next action, or C{None} when a replay is exhausted.

        @return: Action dict, or C{None}.
        @rtype: dict or C{None}
        """
        if self._kind == "constant":
            return self._constant
        if self._kind == "random":
            return self._space.sample()
        if self._cursor >= len(self._replay):
            self.exhausted = True
            return None
        action = self._replay[self._cursor]
        self._cursor += 1
        return action


# ---------------------------------------------------------------------------
# Episode runner
# ---------------------------------------------------------------------------


def run_episode(
    config: EnvConfig,
    policy_spec: dict[str, Any],
    run_dir: str | Path,
    *,
    max_steps: int = 10_000,
    seed: int | None = None,
) -> dict[str, Any]:
    """Run one episode of *config* under a declarative policy.

    Writes C{trajectory.jsonl} (one JSON line per step: action, reward,
    reward components, observation) and C{summary.json} into *run_dir*,
    which is created if needed and must be user-visible (CLAUDE.md
    §4.1).

    @param config: The environment config to build and run.
    @type config: L{EnvConfig}
    @param policy_spec: Policy spec; see L{_Policy.__init__}.
    @type policy_spec: dict
    @param run_dir: Directory for episode artifacts.
    @type run_dir: str or L{Path}
    @param max_steps: Hard safety cap on env steps for this call.
    @type max_steps: int
    @param seed: Optional reset seed.
    @type seed: int or C{None}
    @return: Episode summary (steps, totals, per-term components,
        termination flags, artifact paths).
    @rtype: dict
    @raise ToolError: Propagated from
        L{build_env<openswmm_mcp.gym_support.config.build_env>} or
        wrapped engine failures.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    trajectory_path = run_dir / "trajectory.jsonl"
    summary_path = run_dir / "summary.json"

    env = build_env(config)
    try:
        obs, _info = env.reset(seed=seed)
        policy = _Policy(env.action_space, policy_spec)

        total_reward: Any = None
        components_total: dict[str, float] = {}
        steps = 0
        terminated = truncated = False

        with trajectory_path.open("w", encoding="utf-8") as fh:
            while steps < max_steps and not (terminated or truncated):
                action = policy.next_action()
                if action is None:  # replay exhausted
                    break
                obs, reward, terminated, truncated, info = env.step(action)
                steps += 1
                reward_j = json_safe(reward)
                total_reward = (
                    reward_j
                    if total_reward is None
                    else json_safe(np.asarray(total_reward) + np.asarray(reward))
                )
                for term, value in info.get("reward_components", {}).items():
                    components_total[term] = components_total.get(term, 0.0) + float(value)
                fh.write(
                    json.dumps(
                        {
                            "env_step": steps,
                            "action": json_safe(action),
                            "reward": reward_j,
                            "reward_components": json_safe(
                                info.get("reward_components", {})
                            ),
                            "elapsed_days": json_safe(info.get("elapsed_days")),
                            "observation": json_safe(obs),
                        }
                    )
                    + "\n"
                )

        summary = {
            "steps": steps,
            "total_reward": total_reward,
            "reward_components_total": components_total,
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "replay_exhausted": policy.exhausted,
            "hit_max_steps": steps >= max_steps and not (terminated or truncated),
            "policy": {k: v for k, v in policy_spec.items() if k != "actions"},
            "seed": seed,
            "artifacts": [str(trajectory_path), str(summary_path)],
        }
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary
    finally:
        env.close()


# ---------------------------------------------------------------------------
# Interactive env manager
# ---------------------------------------------------------------------------


@dataclass
class EnvHandle:
    """One open interactive env.

    @ivar env_id: Caller-chosen identifier.
    @ivar env: The live (wrapped) C{gymnasium.Env}.
    @ivar config: The config it was built from.
    @ivar lock: Serialises reset/step/close on this env.
    @ivar last_used: Monotonic timestamp of last access.
    @ivar step_count: Env steps since the last reset.
    """

    env_id: str
    env: Any
    config: EnvConfig
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = field(default_factory=time.monotonic)
    step_count: int = 0

    def touch(self) -> None:
        """Refresh L{last_used} to now."""
        self.last_used = time.monotonic()


class EnvManager:
    """Registry of open interactive envs for the C{gym_env_*} tools.

    Mirrors the model C{SessionManager}: explicit lifecycle with a
    capacity cap, plus a lazy idle sweep — any operation first closes
    envs idle longer than I{idle_timeout_s}, so leaked envs (an LLM
    that forgot C{gym_env_close}) do not pin engine handles forever.

    @ivar max_envs: Maximum simultaneously open envs.
    @ivar idle_timeout_s: Idle seconds before an env is swept.
    """

    def __init__(self, max_envs: int = 4, idle_timeout_s: float = 3600.0) -> None:
        """
        @param max_envs: Maximum simultaneously open envs.
        @type max_envs: int
        @param idle_timeout_s: Idle seconds before a sweep closes an env.
        @type idle_timeout_s: float
        """
        self.max_envs = max_envs
        self.idle_timeout_s = idle_timeout_s
        self._envs: dict[str, EnvHandle] = {}
        self._registry_lock = threading.Lock()

    def _sweep(self) -> None:
        """Close envs idle longer than the timeout (internal)."""
        now = time.monotonic()
        for env_id in [
            eid
            for eid, h in self._envs.items()
            if now - h.last_used > self.idle_timeout_s
        ]:
            handle = self._envs.pop(env_id)
            try:
                handle.env.close()
            except Exception:
                pass  # idle cleanup must never mask the caller's operation

    def open(self, env_id: str, config: EnvConfig) -> EnvHandle:
        """Build an env from *config* and register it under *env_id*.

        @param env_id: New identifier; must not already be open.
        @type env_id: str
        @param config: Config to build.
        @type config: L{EnvConfig}
        @return: The new handle.
        @rtype: L{EnvHandle}
        @raise ToolError: C{VALIDATION_ERROR} for duplicate IDs;
            C{MAX_SESSIONS_REACHED} at capacity; build errors propagate.
        """
        with self._registry_lock:
            self._sweep()
            if env_id in self._envs:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Env '{env_id}' is already "
                    "open. Close it first or choose another env_id."
                )
            if len(self._envs) >= self.max_envs:
                raise ToolError(
                    f"[{ErrorCode.MAX_SESSIONS_REACHED}] {self.max_envs} envs "
                    "already open. Close one with gym_env_close."
                )
            env = build_env(config)
            handle = EnvHandle(env_id=env_id, env=env, config=config)
            self._envs[env_id] = handle
            return handle

    def get(self, env_id: str) -> EnvHandle:
        """Return the handle for *env_id*.

        @param env_id: The identifier to look up.
        @type env_id: str
        @return: The handle (with L{EnvHandle.last_used} refreshed).
        @rtype: L{EnvHandle}
        @raise ToolError: C{SESSION_NOT_FOUND} for unknown IDs.
        """
        with self._registry_lock:
            self._sweep()
            handle = self._envs.get(env_id)
            if handle is None:
                open_ids = ", ".join(sorted(self._envs)) or "<none>"
                raise ToolError(
                    f"[{ErrorCode.SESSION_NOT_FOUND}] No open env '{env_id}'. "
                    f"Open envs: {open_ids}."
                )
            handle.touch()
            return handle

    def close(self, env_id: str) -> None:
        """Close and deregister *env_id*.

        @param env_id: The identifier to close.
        @type env_id: str
        @raise ToolError: C{SESSION_NOT_FOUND} for unknown IDs.
        """
        with self._registry_lock:
            handle = self._envs.pop(env_id, None)
        if handle is None:
            raise ToolError(
                f"[{ErrorCode.SESSION_NOT_FOUND}] No open env '{env_id}'."
            )
        with handle.lock:
            handle.env.close()

    def close_all(self) -> None:
        """Close every open env (server shutdown)."""
        with self._registry_lock:
            handles = list(self._envs.values())
            self._envs.clear()
        for handle in handles:
            try:
                handle.env.close()
            except Exception:
                pass

    def list(self) -> list[dict[str, Any]]:
        """Return metadata for every open env.

        @return: One dict per env: id, env_type, inp_path, step_count,
            idle seconds.
        @rtype: list of dict
        """
        with self._registry_lock:
            self._sweep()
            now = time.monotonic()
            return [
                {
                    "env_id": h.env_id,
                    "env_type": h.config.env_type,
                    "inp_path": h.config.inp_path,
                    "step_count": h.step_count,
                    "idle_seconds": round(now - h.last_used, 1),
                }
                for h in self._envs.values()
            ]
