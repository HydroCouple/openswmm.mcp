"""Typed stub for L{openswmm_mcp.gym_support.envs}.

@author: Caleb Buahin
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openswmm_mcp.gym_support.config import EnvConfig

def json_safe(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays to plain JSON types.

    @rtype: object
    """

def build_action(action_space: Any, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Convert a JSON action payload into a valid Dict-space action.

    Missing leaves default to the Box midpoint; values are clipped.

    @raise ToolError: C{VALIDATION_ERROR} on unknown keys/wrong shapes.
    @rtype: dict
    """

def run_episode(
    config: EnvConfig,
    policy_spec: dict[str, Any],
    run_dir: str | Path,
    *,
    max_steps: int = ...,
    seed: int | None = ...,
) -> dict[str, Any]:
    """Run one episode under a declarative policy, writing artifacts.

    Writes C{trajectory.jsonl} and C{summary.json} into *run_dir*.

    @param policy_spec: C{{"kind": "constant"|"random"|"replay", ...}}.
    @type policy_spec: dict
    @raise ToolError: From build_env or policy validation.
    @return: Episode summary (steps, totals, components, flags, paths).
    @rtype: dict
    """

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
    lock: threading.Lock
    last_used: float
    step_count: int

    def touch(self) -> None:
        """Refresh L{last_used} to now."""

class EnvManager:
    """Registry of open interactive envs with lazy idle sweeps.

    @ivar max_envs: Maximum simultaneously open envs.
    @ivar idle_timeout_s: Idle seconds before an env is swept.
    """

    max_envs: int
    idle_timeout_s: float

    def __init__(self, max_envs: int = ..., idle_timeout_s: float = ...) -> None: ...
    def open(self, env_id: str, config: EnvConfig) -> EnvHandle:
        """Build and register an env.

        @raise ToolError: C{VALIDATION_ERROR} (duplicate) or
            C{MAX_SESSIONS_REACHED} (capacity).
        @rtype: L{EnvHandle}
        """

    def get(self, env_id: str) -> EnvHandle:
        """Return the handle, refreshing its idle timestamp.

        @raise ToolError: C{SESSION_NOT_FOUND}.
        @rtype: L{EnvHandle}
        """

    def close(self, env_id: str) -> None:
        """Close and deregister.

        @raise ToolError: C{SESSION_NOT_FOUND}.
        """

    def close_all(self) -> None:
        """Close every open env (server shutdown)."""

    def list(self) -> list[dict[str, Any]]:
        """Metadata for every open env.

        @rtype: list of dict
        """
