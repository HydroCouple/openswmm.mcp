"""Typed stub for L{openswmm_mcp.gym_support.jobs}.

@author: Caleb Buahin
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from openswmm_mcp.gym_support.config import EnvConfig

JobState = Literal["pending", "running", "done", "failed", "cancelled"]

class OptimizationConfig(BaseModel):
    """Declarative optimization-run settings.

    @ivar algorithm: C{"random_search"}, C{"grid_search"}, or a Platypus
        MOEA name (C{"nsga2"}, C{"nsga3"}, C{"spea2"}, C{"moead"},
        C{"gde3"}).
    @ivar budget: Maximum design evaluations.
    @ivar population_size: MOEA population size.
    @ivar grid_levels: Per-dimension levels for grid_search.
    @ivar seed: RNG / reset seed.
    @ivar max_steps_per_episode: Safety cap per evaluation episode.
    """

    algorithm: str
    budget: int
    population_size: int
    grid_levels: int
    seed: int | None
    max_steps_per_episode: int

@dataclass(frozen=True)
class DesignDimension:
    """One design factory's slice of the search space.

    @ivar key: Action-space key (factory C{name}).
    @ivar labels: Per-component C{"<kind>:<element_id>"} labels.
    @ivar low: Lower bound — scalar (broadcast) or per-component tuple.
    @ivar high: Upper bound — scalar (broadcast) or per-component tuple.
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

def design_dimensions(env_config: EnvConfig) -> list[DesignDimension]:
    """Derive labeled search-space dimensions from design factories.

    @raise ToolError: C{VALIDATION_ERROR} without design factories.
    @rtype: list of L{DesignDimension}
    """

@dataclass
class Job:
    """One optimization job and its mutable progress/result state."""

    job_id: str
    env_config: EnvConfig
    opt_config: OptimizationConfig
    output_dir: Path
    state: JobState
    evaluations_done: int
    error: str | None
    result: dict[str, Any] | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    cancel_event: threading.Event
    lock: threading.Lock

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe progress snapshot.

        @rtype: dict
        """

class JobManager:
    """Background thread pool plus job records (plan §7.3: 2 workers).

    @ivar max_workers: Concurrent job cap.
    """

    max_workers: int

    def __init__(self, max_workers: int = ...) -> None: ...
    def start(
        self,
        env_config: EnvConfig,
        opt_config: OptimizationConfig,
        output_dir: str | Path | None = ...,
    ) -> dict[str, Any]:
        """Validate, register, submit; return the pending snapshot.

        @raise ToolError: C{VALIDATION_ERROR} (unknown algorithm or no
            design factories).
        @rtype: dict
        """

    def get(self, job_id: str) -> dict[str, Any]:
        """Progress snapshot.

        @raise ToolError: C{ELEMENT_NOT_FOUND}.
        @rtype: dict
        """

    def list(self) -> list[dict[str, Any]]:
        """All job snapshots, newest first.

        @rtype: list of dict
        """

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cooperative cancel (between evaluations).

        @raise ToolError: C{ELEMENT_NOT_FOUND}.
        @rtype: dict
        """

    def results(self, job_id: str) -> dict[str, Any]:
        """Full results of a finished job.

        @raise ToolError: C{ELEMENT_NOT_FOUND} or C{INVALID_STATE}.
        @rtype: dict
        """

    def shutdown(self) -> None:
        """Cancel all jobs and stop the pool."""
