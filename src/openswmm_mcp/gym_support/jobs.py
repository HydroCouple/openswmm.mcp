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

"""Background optimization jobs over design spaces.

A L{JobManager} owns a small thread pool (default C{max_workers=2}) of job
runners. Each job runs L{openswmm_gymnasium.optimize.run_study}, which
asks its optimiser for batches of candidate designs and evaluates each
batch in parallel on worker processes (C{OptimizationConfig.workers};
the engine is loaded once per worker and a crashing evaluation cannot take
this server down). Algorithms:

  - C{random_search} — uniform sampling baseline.
  - C{grid_search}   — full factorial over per-dimension levels,
    truncated at the budget.
  - C{nsga2}, C{nsga3}, C{spea2}, C{moead}, C{gde3} — Platypus MOEAs for
    multi-objective design search, via the optional C{platypus-opt} extra.

Cancellation is cooperative — checked between evaluations. Every job
writes reviewable artifacts (C{job.json}, C{evaluations.jsonl},
C{result.json}) under a user-visible output directory (CLAUDE.md §4.1).

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: Apache-2.0
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from openswmm_gymnasium.optimize import (
    ALGORITHMS,
    DesignDimension,
    OptimizationConfig,
    StudyCancelled,
    run_study,
)
from openswmm_gymnasium.optimize import design_dimensions as _design_dimensions
from openswmm_gymnasium.spec import SpecError
from openswmm_gymnasium.spec.config import EnvConfig

from openswmm_mcp.errors import ErrorCode, ToolError

__all__ = ["DesignDimension", "Job", "JobManager", "OptimizationConfig", "design_dimensions"]

JobState = Literal["pending", "running", "done", "failed", "cancelled"]


def design_dimensions(env_config: EnvConfig) -> list[DesignDimension]:
    """Labelled search-space dimensions of a config (see L{openswmm_gymnasium.optimize}).

    @raise ToolError: C{VALIDATION_ERROR} when the config has nothing to search.
    """
    try:
        return _design_dimensions(env_config)
    except SpecError as exc:
        raise ToolError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Job record
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """One optimization job and its mutable progress/result state.

    @ivar job_id: Unique identifier returned by C{gym_job(action='start')}.
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


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class JobManager:
    """Owns the job-runner threads and all job records.

    @ivar max_workers: Pool size (default: 2).
    """

    def __init__(self, max_workers: int = 2) -> None:
        """
        @param max_workers: Concurrent job cap; each job runs its own pool
            of evaluation worker processes.
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
            C{<inp_dir>/gym_runs/<job_id>}.
        @type output_dir: str, L{Path}, or C{None}
        @return: Initial snapshot (C{state == "pending"}).
        @rtype: dict
        @raise ToolError: C{VALIDATION_ERROR} on unknown algorithms or
            configs without design factories.
        """
        algo = opt_config.algorithm
        if algo not in ALGORITHMS:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown algorithm '{algo}'. "
                f"Valid algorithms: {', '.join(ALGORITHMS)}."
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

        # Persist the full job definition for review.
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

        def progress(record: dict[str, Any]) -> None:
            with job.lock:
                job.evaluations_done = record["evaluation"]

        try:
            result = run_study(
                job.env_config,
                job.opt_config,
                job.output_dir,
                should_stop=job.cancel_event.is_set,
                on_evaluation=progress,
            )
            with job.lock:
                job.result = result
                job.state = "done"
        except StudyCancelled:
            with job.lock:
                job.state = "cancelled"
        except Exception as exc:  # job thread must never die silently
            with job.lock:
                job.state = "failed"
                job.error = (
                    str(exc) if isinstance(exc, SpecError) else f"{type(exc).__name__}: {exc}"
                )
        finally:
            with job.lock:
                job.finished_at = time.time()

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

        Used by C{gym_score(action='apply_design')} to map decision vectors back onto
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
