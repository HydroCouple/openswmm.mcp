"""Platform-format manifests for a local run (ecosystem plan, work package 2.4).

A local ``run`` that starts from an opened model writes, under
``<session folder>/local/<run id>/``, what a platform run publishes: the model as it ran
(every edit included) packaged with its revision manifest, and an ``attempt/`` folder
holding ``attempt.json``, ``run.json``, ``summary.json``, ``model.rpt`` and ``model.out``.
The attempt's actor names the journal's session and the tool call that started the run,
so the water ontology's ``platform_to_rows`` loads a local run and its journal the way it
loads a platform run. It is opt-in (``OPENSWMM_MCP_LOCAL_MANIFESTS=true``) and uses
hydrocouple.platform's base install (the ``platform`` extra); otherwise nothing is written.
Forcing and other writes made while the run steps are not in the model file: the journal
records them.
"""

from __future__ import annotations

import getpass
import hashlib
import platform as _platform
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openswmm_mcp import __version__

WORKFLOW, WORKFLOW_VERSION = "openswmm.mcp.run", "1"


def available() -> bool:
    try:
        import hydrocouple_platform.contracts.manifests  # noqa: F401
        import hydrocouple_platform.packaging  # noqa: F401
    except ImportError:
        return False
    return True


def local_identity() -> str:
    """The person a local session acts for: the operating-system login (decision G)."""
    return getpass.getuser()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def begin(session: Any, tool_call_id: str | None) -> dict[str, Any]:
    """Snapshot and package the model about to run; the record ``finish`` completes.

    @return: The run record, or ``{"error": ...}`` when the model cannot be packaged.
    """
    from hydrocouple_platform.packaging import package_swmm_model
    from hydrocouple_platform.packaging.package import PackagingError

    run_id = f"run_local_{uuid.uuid4().hex[:20]}"
    folder = Path(session.working_dir) / "local" / run_id
    folder.mkdir(parents=True)
    snapshot = folder / "model-as-run.inp"
    session.require_solver().write(str(snapshot))
    try:
        package = package_swmm_model(snapshot, folder / "package")
    except PackagingError as exc:
        return {"error": f"not packaged: {'; '.join(exc.problems)}"}
    return {
        "run_id": run_id,
        "folder": folder,
        "package": package,
        "tool_call_id": tool_call_id,
        "started_at": datetime.now(timezone.utc),
    }


def finish(
    session: Any, record: dict[str, Any], quality: dict[str, Any], journal: Any
) -> dict[str, Any]:
    """Write the attempt of a finished local run; return where its manifests are."""
    import openswmm
    from hydrocouple_platform import __version__ as platform_version
    from hydrocouple_platform.contracts.jobs import Actor
    from hydrocouple_platform.contracts.manifests import (
        ArtifactEntry,
        ArtifactKind,
        AttemptManifest,
        Provenance,
        RuntimeVersions,
        canonical_json,
    )
    from hydrocouple_platform.runner.summary import engine_summary

    folder, package = record["folder"], record["package"]
    attempt = folder / "attempt"
    attempt.mkdir()
    engine = getattr(openswmm, "__version__", None)
    finished = datetime.now(timezone.utc)
    files = package.manifest.model_dump(mode="json", by_alias=True)["files"]
    revision_sha = package.manifest.digest()
    suffix = record["run_id"].removeprefix("run_local_")
    shutil.copyfile(session.rpt_path, attempt / "model.rpt")
    shutil.copyfile(session.out_path, attempt / "model.out")
    (attempt / "summary.json").write_bytes(
        canonical_json(
            engine_summary(session.require_solver(), engine).model_dump(mode="json", by_alias=True)
        )
    )
    (attempt / "run.json").write_bytes(
        canonical_json(
            {
                "schema": "openswmm.mcp/local-run/1",
                "run_id": record["run_id"],
                "revision_manifest_sha256": revision_sha,
                "engine": engine,
                "started_at": record["started_at"].isoformat(),
                "finished_at": finished.isoformat(),
            }
        )
    )
    kinds = {
        "model.rpt": (ArtifactKind.REPORT, "swmm_rpt"),
        "model.out": (ArtifactKind.BINARY_OUTPUT, "swmm_out"),
        "summary.json": (ArtifactKind.SUMMARY, "json"),
    }
    artifacts = [
        ArtifactEntry(
            path=name,
            sha256=_sha256(attempt / name),
            size=(attempt / name).stat().st_size,
            kind=kind,
            format=fmt,
        )
        for name, (kind, fmt) in kinds.items()
    ]
    session_id = journal.journal_id if journal is not None else f"local_{suffix}"
    manifest = AttemptManifest(
        run_manifest_sha256=_sha256(attempt / "run.json"),
        status="completed",
        artifacts=[
            ArtifactEntry(
                path="run.json",
                sha256=_sha256(attempt / "run.json"),
                size=(attempt / "run.json").stat().st_size,
                kind=ArtifactKind.RUN_MANIFEST,
                format="json",
            ),
            *artifacts,
        ],
        quality=quality,
        provenance=Provenance(
            run_id=record["run_id"],
            job_id=f"job_local_{suffix}",
            attempt_id=f"att_local_{suffix}",
            attempt_number=1,
            revision_id=f"rev_local_{revision_sha[:20]}",
            revision_manifest_sha256=revision_sha,
            composition_sha256=next(f["sha256"] for f in files if f["role"] == "composition"),
            inputs={f["path"]: f["sha256"] for f in files},
            workflow=WORKFLOW,
            workflow_version=WORKFLOW_VERSION,
            parameters={"session_id": session.session_id},
            threads=1,
            runtime=RuntimeVersions(
                platform=platform_version, python=_platform.python_version(), engine=engine
            ),
            actor=Actor(
                human_subject=local_identity(),
                agent_session_id=session_id,
                agent_client="openswmm.mcp",
                agent_client_version=__version__,
                tool_call_id=record["tool_call_id"],
            ),
            started_at=record["started_at"],
            finished_at=finished,
        ),
    )
    (attempt / "attempt.json").write_bytes(
        canonical_json(manifest.model_dump(mode="json", by_alias=True))
    )
    return {
        "run_id": record["run_id"],
        "revision_manifest": str(folder / "package" / "revision-manifest.json"),
        "attempt": str(attempt),
        "journal": str(journal.path) if journal is not None else None,
    }
