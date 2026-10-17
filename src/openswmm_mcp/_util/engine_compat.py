"""Workarounds for released engine bugs that are fixed upstream but not yet on PyPI.

openswmm <= 6.0.0a4.dev1: ``get_report_snapshot`` raises ``BadParamError`` on a model
with no subcatchments, because the subcatchment statistics wrappers pass a zero count
to C bulk getters that reject it. Fixed upstream in openswmm.engine 5925593d
(branch swmm6_rel); remove this module once a release with that fix is required.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
from openswmm.engine import BadParamError, _statistics, get_report_snapshot

_swap_lock = threading.Lock()


class _ZeroSafeStatistics(_statistics.Statistics):
    """``Statistics`` whose subcatchment arrays are empty when there are no subcatchments."""

    def __init__(self, solver: Any) -> None:
        super().__init__(solver)
        self._n_subcatchments = len(solver.subcatchments)

    @property
    def subcatchment_runoff_vol(self):
        if self._n_subcatchments == 0:
            return np.empty(0)
        return super().subcatchment_runoff_vol

    @property
    def subcatchment_max_runoff(self):
        if self._n_subcatchments == 0:
            return np.empty(0)
        return super().subcatchment_max_runoff


def report_snapshot(solver: Any):
    """``get_report_snapshot(solver)``, also for models without subcatchments.

    The snapshot code imports ``Statistics`` when it runs, so the zero-safe subclass is
    swapped in for that one call. It behaves identically when subcatchments exist, so
    a concurrent snapshot of another session during the swap is unaffected.
    """
    try:
        return get_report_snapshot(solver)
    except BadParamError:
        if len(solver.subcatchments) > 0:
            raise
    with _swap_lock:
        original = _statistics.Statistics
        _statistics.Statistics = _ZeroSafeStatistics
        try:
            return get_report_snapshot(solver)
        finally:
            _statistics.Statistics = original
