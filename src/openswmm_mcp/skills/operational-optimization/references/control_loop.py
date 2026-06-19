"""Reference skeleton for the agent-based capacity-market controller.

This file is a *reference*, not a runnable program. It encodes the logic that
the operational-optimization skill orchestrates over the OpenSWMM MCP tools:
normalized cost curves, a direct-acting PID per trade route, buyer/seller
matching on the cost differential, and the stepwise reactive loop.

The pure logic (cost curves, PID, matching) is complete and correct. The three
``Adapter`` methods are the only I/O seams: in a live run the skill maps them to
MCP tool calls (noted inline). Keeping I/O behind the adapter lets the same
logic be exercised by the gym env or an outer NSGA-II loop during tuning.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Protocol


# ---------------------------------------------------------------------------
# Cost curves -- map a stress metric in [0, 1] to a price in [0, 1]
# ---------------------------------------------------------------------------


def price_from_curve(metric: float, curve: dict) -> float:
    """Return a normalized price in [0, 1] for a stress *metric* in [0, 1].

    ``curve`` follows the market_config schema (type/onset/full/steepness/
    floor/ceiling). Both shapes are monotonic non-decreasing in the metric, so
    a more-stressed agent never prices lower than a less-stressed one.
    """
    m = _clamp(metric, 0.0, 1.0)
    floor = curve.get("floor", 0.0)
    ceiling = curve.get("ceiling", 1.0)
    onset = curve["onset"]

    if curve["type"] == "piecewise_linear":
        full = curve.get("full", 1.0)
        if m <= onset:
            frac = 0.0
        elif m >= full or full <= onset:
            frac = 1.0
        else:
            frac = (m - onset) / (full - onset)
    elif curve["type"] == "logistic":
        steepness = curve.get("steepness", 10.0)
        frac = 1.0 / (1.0 + math.exp(-steepness * (m - onset)))
    else:  # pragma: no cover - schema validation should prevent this
        raise ValueError(f"unknown curve type: {curve['type']!r}")

    return _clamp(floor + (ceiling - floor) * frac, 0.0, 1.0)


def aggregate(prices: list[float], how: str) -> float:
    """Combine one side's agent prices into a single side price."""
    if not prices:
        return 0.0
    return max(prices) if how == "max" else sum(prices) / len(prices)


# ---------------------------------------------------------------------------
# PID -- direct-acting (output rises with the cost differential)
# ---------------------------------------------------------------------------


@dataclass
class PID:
    kp: float
    ki: float = 0.0
    kd: float = 0.0
    setpoint: float = 0.0
    out_min: float = 0.0
    out_max: float = 1.0
    _integral: float = field(default=0.0, repr=False)
    _prev_error: float | None = field(default=None, repr=False)

    def update(self, measurement: float, dt: float) -> float:
        """Advance the controller by *dt* seconds and return the new setting.

        ``measurement`` is the cost differential (buyer_price - seller_price).
        Error is ``measurement - setpoint`` so the output increases when the
        buyer is more stressed than the seller. Integration is clamped
        (conditional anti-windup) so a saturated actuator does not wind up.
        """
        error = measurement - self.setpoint
        derivative = 0.0 if self._prev_error is None else (error - self._prev_error) / dt

        candidate_i = self._integral + error * dt
        raw = self.kp * error + self.ki * candidate_i + self.kd * derivative
        output = _clamp(raw, self.out_min, self.out_max)

        # Anti-windup: only accumulate when not pushing further into saturation.
        if self.out_min < raw < self.out_max or (raw <= self.out_min and error < 0) or (
            raw >= self.out_max and error > 0
        ):
            self._integral = candidate_i

        self._prev_error = error
        return output


# ---------------------------------------------------------------------------
# I/O seam -- map these to MCP tools in a live run
# ---------------------------------------------------------------------------


class Adapter(Protocol):
    def read_metrics(self) -> dict[str, float]:
        """Return {agent_id: stress_metric in [0,1]} for the current step.

        Live mapping: nodes_get_depths_bulk / nodes_get_overflows_bulk /
        links_get_depths_bulk / links_get_flows_bulk, plus the storage curve to
        turn depth into a fill fraction. Normalize each raw value to [0,1] per
        the agent's stress_metric before returning.
        """

    def apply_setting(self, structure_link_id: str, setting: float) -> None:
        """Apply a [0,1] setting to a structure.

        Live mapping: links_set_target_setting (gates/orifices/weirs; requires
        the session 'running'), pump setpoints via links_set_pump_startup_depth
        / _shutoff_depth / links_set_target_setting, or forcing_set_link_control.
        """

    def step(self, dt_seconds: float) -> bool:
        """Advance the simulation by *dt_seconds*; return True when complete.

        Live mapping: lifecycle_stride / lifecycle_step_simulation (auto-starts
        the solver), then lifecycle_get_simulation_state to detect completion.
        """


# ---------------------------------------------------------------------------
# Reactive control loop
# ---------------------------------------------------------------------------


def run(config: dict, adapter: Adapter) -> list[dict]:
    """Run the market controller to completion; return the per-step trace.

    Each trace row records the time, every side price, the cost differential,
    and the applied setting -- the raw material for the HTML report figures.
    """
    curves = config["cost_curves"]
    agent_curve = {a["id"]: curves[a["curve"]] for a in config["agents"]}
    routes = config["trade_routes"]
    pids = {r["structure_link_id"]: PID(**r["pid"]) for r in routes}
    dt = config["meta"]["control_interval_seconds"]

    trace: list[dict] = []
    t = 0.0
    done = False
    while not done:
        metrics = adapter.read_metrics()
        prices = {aid: price_from_curve(metrics.get(aid, 0.0), c) for aid, c in agent_curve.items()}

        row: dict = {"t": t}
        for r in routes:
            how = r.get("aggregate", "max")
            buyer = aggregate([prices[a] for a in r["buyer_agents"]], how)
            seller = aggregate([prices[a] for a in r["seller_agents"]], how)
            differential = buyer - seller

            link = r["structure_link_id"]
            setting = pids[link].update(differential, dt)

            # Enforce per-route hard limits before actuating.
            c = r.get("constraints", {})
            setting = _clamp(setting, c.get("setting_min", 0.0), c.get("setting_max", 1.0))
            # NOTE: min_cycle_seconds / max_starts_per_hour for pumps must also
            # be enforced here (track last switch time per route) before apply.

            adapter.apply_setting(link, setting)
            row[link] = {"buyer": buyer, "seller": seller, "diff": differential, "setting": setting}

        trace.append(row)
        done = adapter.step(dt)
        t += dt

    return trace


# ---------------------------------------------------------------------------
# Scoring (for tuning) -- objectives are minimized
# ---------------------------------------------------------------------------


def score(config: dict) -> dict[str, float]:
    """Return the four objective costs after a completed run.

    Live mapping: analysis_get_report_snapshot (flooding volume, pump summary),
    analysis_get_flooding_summary, the storage volume summary, and the
    untreated-outfall discharge integral (nodes_get_tag + discharge series).
    NSGA-II minimizes these; weights in config['objectives'] are applied by the
    caller when collapsing to a scalar for single-objective baselining.
    """
    raise NotImplementedError("wire to analysis_* tools at call time")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
