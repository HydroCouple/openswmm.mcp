"""``platform_study``: an optimization on the platform, proposed by the agent or by a
gymnasium optimizer this server runs for it.

A study is the platform's registered evaluation function: a revision, the decision space and
objectives of a gymnasium env config, a seed and an evaluation budget. The agent can propose
candidates itself (``submit``), or start a gymnasium optimizer here (``optimize``) that
proposes them batch by batch until its budget is spent. Every platform request names the
tool call behind it, so each evaluation traces back to the call that proposed it.

An ``optimize`` runs in this server's process. It does not survive a restart of the server;
its study does, and ``status`` with the study id keeps reporting it.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastmcp import Context

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.journal import CURRENT_CALL
from openswmm_mcp.tools.platform import _folder, _platform, _project

#: Where ``platform_model upload`` puts the model inside a revision.
DEFAULT_MODEL_PATH = "model/model.inp"
_ENDED = {"COMPLETED", "FAILED", "CANCELLED"}


class Optimization:
    """A gymnasium optimizer this server runs against one platform study."""

    def __init__(self, optimization_id: str, output_dir: Path) -> None:
        self.optimization_id, self.output_dir = optimization_id, output_dir
        self.state, self.study_id, self.evaluations = "STARTING", None, 0
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.stop = threading.Event()

    def view(self) -> dict[str, Any]:
        return {
            "optimization_id": self.optimization_id,
            "state": self.state,
            "study_id": self.study_id,
            "evaluations": self.evaluations,
            "output_dir": str(self.output_dir),
            "error": self.error,
        }


def _inputs(env_config: dict | None, optimization: dict | None) -> tuple[Any, Any]:
    from openswmm_gymnasium.optimize import OptimizationConfig
    from openswmm_gymnasium.spec.config import EnvConfig

    if env_config is None or optimization is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] This action needs env_config (a gymnasium env "
            "config) and optimization (algorithm, budget, seed, ...)."
        )
    try:
        return EnvConfig.model_validate(env_config), OptimizationConfig.model_validate(optimization)
    except ValueError as exc:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {exc}") from None


def _request(
    settings: ServerSettings,
    env: Any,
    opt: Any,
    revision_id: str | None,
    target_id: str | None,
    model_path: str,
    pool_size: int,
    project_id: str | None,
    session_id: str | None,
    tool_call_id: str,
    options: dict[str, Any],
) -> Any:
    from hydrocouple_platform.contracts.studies import StudyCreate
    from hydrocouple_platform.gym import study_request

    if not revision_id or not target_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] This action needs revision_id and target_id."
        )
    request = study_request(
        env,
        opt,
        project_id=_project(settings, project_id),
        revision_id=revision_id,
        target_id=target_id,
        inp_path=model_path,
        session_id=session_id,
        pool_size=pool_size,
        tool_call_id=tool_call_id,
    )
    if options:
        request = StudyCreate.model_validate({**request.model_dump(), **options})
    return request


def _dimensions(request: Any) -> list[dict[str, Any]]:
    """The decision vector's order: a candidate lists one value per dimension, in this order."""
    return [d.model_dump(exclude_none=True) for d in request.dimensions]


def _optimize(
    run: Optimization,
    client: Any,
    env: Any,
    opt: Any,
    request: Any,
    model_path: str,
    tool_call_id: str,
    options: dict[str, Any],
) -> None:
    from hydrocouple_platform.contracts.errors import PlatformError
    from hydrocouple_platform.gym import run_platform_study
    from openswmm_gymnasium.optimize import StudyCancelled

    def created(study: dict) -> None:
        run.study_id, run.state = study["study_id"], "RUNNING"

    def evaluated(_record: dict) -> None:
        run.evaluations += 1

    try:
        run.result = run_platform_study(
            env,
            opt,
            run.output_dir,
            client=client,
            project_id=request.project_id,
            revision_id=request.revision_id,
            target_id=request.target_id,
            inp_path=model_path,
            session_id=request.session_id,
            pool_size=request.pool_size,
            idempotency_key=f"optimize-{tool_call_id}",
            should_stop=run.stop.is_set,
            on_evaluation=evaluated,
            tool_call_id=tool_call_id,
            on_study=created,
            study_options=options,
        )
        run.state = "COMPLETED"
    except StudyCancelled:
        run.state = "CANCELLED"
    except PlatformError as exc:
        run.state = "FAILED"
        run.error = f"[{exc.body.code.value}] {exc.body.message}"
        if exc.body.next_action:
            run.error += f" Next: {exc.body.next_action}"
    except Exception as exc:  # reported through status, never raised into the server
        run.state, run.error = "FAILED", f"{type(exc).__name__}: {exc}"


async def platform_study(
    ctx: Context,
    action: Literal[
        "estimate", "create", "submit", "optimize", "status", "results", "close", "cancel"
    ],
    study_id: str | None = None,
    optimization_id: str | None = None,
    batch_id: str | None = None,
    revision_id: str | None = None,
    target_id: str | None = None,
    env_config: dict[str, Any] | None = None,
    optimization: dict[str, Any] | None = None,
    model_path: str = DEFAULT_MODEL_PATH,
    pool_size: int = 1,
    candidates: list[list[float]] | None = None,
    wait_seconds: float = 0.0,
    estimate_id: str | None = None,
    estimated_seconds_per_evaluation: float | None = None,
    project_id: str | None = None,
    session_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """An optimization on the platform: a study over a registered revision.

    - ``estimate``: count and price a would-be study (evaluations, pool, profile, cost band,
      whether a person must approve it) and keep the estimate for ``estimate_id``.
    - ``create``: register a study from env_config and optimization (its budget is the
      evaluation budget); returns the study and its dimensions, in candidate order.
    - ``submit``: queue candidates you propose (one value per dimension); with
      ``wait_seconds`` wait that long for their costs.
    - ``optimize``: run optimization.algorithm here against a new study until its budget is
      spent; returns at once with an optimization_id to watch.
    - ``status``: an optimization (optimization_id), a batch (study_id, batch_id) or a study.
    - ``results``: the Pareto set or best candidate, from an optimization or a study.
    - ``close``: no more batches (the study finishes the queued ones); ``cancel``: stop now.

    Outside the agent session's budget a study is PENDING_APPROVAL until a person decides.
    Every request names this tool call, so each evaluation traces back to it.
    """
    tool_call_id = CURRENT_CALL.get() or f"tc_{uuid.uuid4().hex}"
    context = ctx.lifespan_context
    runs: dict[str, Optimization] = context.setdefault("optimizations", {})
    options = {
        k: v
        for k, v in (
            ("estimate_id", estimate_id),
            ("estimated_seconds_per_evaluation", estimated_seconds_per_evaluation),
        )
        if v is not None
    }

    if optimization_id is not None and action in ("status", "results", "cancel"):
        run = runs.get(optimization_id)
        if run is None:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] No optimization {optimization_id} in this "
                "server; after a restart, use the study_id instead."
            )
        if action == "cancel":
            run.stop.set()
        if action == "results":
            if run.state != "COMPLETED":
                raise ToolError(
                    f"[{ErrorCode.INVALID_STATE}] Optimization {optimization_id} is {run.state}; "
                    "results come when it completes."
                )
            files = {
                name: str(run.output_dir / name)
                for name in (
                    "result.json",
                    "study-manifest.json",
                    "study-lineage.json",
                    "evaluation-lineage.jsonl",
                    "evaluations.jsonl",
                )
            }
            return run.view() | {"result": run.result, "files": files}
        return run.view()

    if action == "optimize":
        env, opt = _inputs(env_config, optimization)
        settings = context["settings"]
        request = _request(
            settings,
            env,
            opt,
            revision_id,
            target_id,
            model_path,
            pool_size,
            project_id,
            session_id,
            tool_call_id,
            options,
        )
        run_id = f"opt_{uuid.uuid4().hex[:16]}"
        run = Optimization(run_id, _folder(settings, "studies", run_id))
        runs[run_id] = run
        threading.Thread(
            target=_optimize,
            args=(run, context["platform"], env, opt, request, model_path, tool_call_id, options),
            name=f"platform-optimize-{run_id}",
            daemon=True,
        ).start()
        deadline = time.monotonic() + 30
        while run.study_id is None and run.state not in _ENDED and time.monotonic() < deadline:
            await asyncio.sleep(0.1)  # answer with the study id once the platform has it
        if run.study_id is None and run.state == "FAILED":
            # The platform refused the study itself (budget scope, a required estimate, an
            # invalid request): a refusal, as create would give, not a running optimization.
            del runs[run_id]
            raise ToolError(run.error or f"[{ErrorCode.VALIDATION_ERROR}] The study was refused.")
        return run.view() | {"dimensions": _dimensions(request), "tool_call_id": tool_call_id}

    def _run(client: Any, settings: ServerSettings) -> dict:
        if action in ("estimate", "create"):
            env, opt = _inputs(env_config, optimization)
            request = _request(
                settings,
                env,
                opt,
                revision_id,
                target_id,
                model_path,
                pool_size,
                project_id,
                session_id,
                tool_call_id,
                options,
            )
            if action == "estimate":
                return client.estimate_study(request) | {"dimensions": _dimensions(request)}
            study = client.create_study(request, idempotency_key or f"study-{tool_call_id}")
            return study | {"dimensions": _dimensions(request), "tool_call_id": tool_call_id}
        if not study_id:
            raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {action} needs study_id.")
        if action == "submit":
            if not candidates:
                raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] submit needs candidates.")
            batch = client.submit_batch(
                study_id, candidates, idempotency_key or f"batch-{tool_call_id}"
            )
            deadline = time.monotonic() + max(wait_seconds, 0.0)
            while batch["state"] == "QUEUED" and time.monotonic() < deadline:
                time.sleep(0.25)
                batch = client.batch(study_id, batch["batch_id"])
            return batch | {"tool_call_id": tool_call_id}
        if action == "status":
            return client.batch(study_id, batch_id) if batch_id else client.study(study_id)
        if action == "results":
            return client.study_results(study_id)
        if action == "close":
            return client.close_study(study_id)
        return client.cancel_study(study_id)

    return await _platform(ctx, _run)


def stop_all(context: dict[str, Any]) -> None:
    """Ask every optimization this server runs to stop (the server is shutting down)."""
    for run in context.get("optimizations", {}).values():
        run.stop.set()
