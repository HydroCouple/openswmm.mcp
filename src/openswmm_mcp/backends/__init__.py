"""Engine backend abstraction for the OpenSWMM MCP server.

Two backends are supported, selected per-session at open time:

- ``"openswmm"`` (default) — wraps :mod:`openswmm.engine`, the modern engine.
  Full feature set: ModelBuilder, in-place editing, controls, infrastructure,
  quality, spatial, hotstart, output reader, geopackage.
- ``"legacy"`` — wraps :mod:`openswmm.legacy.engine`, the EPA SWMM 5.x C
  bindings shipped alongside the new engine.  Only basic query / forcing /
  hotstart / mass-balance tools are supported; advanced tools raise
  ``NOT_SUPPORTED``.

Tools call ``session.backend.<accessor>`` (or ``session.<accessor>`` thanks
to delegation in :class:`~openswmm_mcp.session.SimSession`) without having
to know which engine is underneath.
"""

from __future__ import annotations

from typing import Literal

from .base import Backend

EngineKind = Literal["openswmm", "legacy"]
"""String tag identifying which backend a session uses."""


def make_backend(
    engine: EngineKind,
    inp_path: str,
    rpt_path: str,
    out_path: str,
) -> Backend:
    """Construct a :class:`Backend` for *engine*.

    Imports are deferred so a missing legacy build does not break openswmm-only
    deployments and vice-versa.
    """
    if engine == "openswmm":
        from .openswmm import OpenSwmmBackend

        return OpenSwmmBackend(inp_path, rpt_path, out_path)
    if engine == "legacy":
        from .legacy import LegacyBackend

        return LegacyBackend(inp_path, rpt_path, out_path)
    raise ValueError(f"Unknown engine '{engine}'. Expected 'openswmm' or 'legacy'.")


__all__ = ["Backend", "EngineKind", "make_backend"]
