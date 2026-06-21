# Optimization-results dashboard — data contract

`optimization_dashboard.template.html` is data-driven. Build the final dashboard
by replacing two placeholders in the template:

- `__PLOTLY_JS__` — inline `plotly.min.js` (offline) or a CDN `<script>` tag.
- `__DATA_JSON__` — `json.dumps(DATA)` where `DATA` matches the shape below.

Objective values are normalized costs in `[0, 1]` (lower is better). `macro` and
`granular[...]` series are arrays aligned to `times`. Each config carries its own
`event_window` so the dashboard can show the adverse window shrinking versus the
baseline.

```jsonc
{
  "times": [0, 0.25, ...],
  "asset_ids": ["C2-3", "N4", ...],              // choices for the granular panel
  "obj_keys":  ["flooding", "cso", "storage", "energy"],
  "obj_labels": { "flooding": "Flooding", "cso": "Uncontrolled discharge",
                  "storage": "Storage under-use", "energy": "Pumping energy" },

  "baseline": {
    "objectives": { "flooding": 0.82, "cso": 0.74, "storage": 0.61, "energy": 0.30 },
    "cost": 1250000,                             // $ (or any consistent unit)
    "macro": { "flooding": [/*…*/], "uncontrolled": [/*…*/] },
    "event_window": [6.75, 16.0],
    "granular": { "C2-3": [/* util over time */], "N4": [/*…*/] }
  },

  "configs": [
    {
      "id": "cfg07",
      "pareto": true,                            // on the non-dominated front?
      "objectives": { "flooding": 0.31, "cso": 0.40, "storage": 0.45, "energy": 0.48 },
      "cost": 720000,
      "savings": { "cost": 530000, "flood_pct": 62.2, "cso_pct": 45.9 },
      "macro": { "flooding": [/*…*/], "uncontrolled": [/*…*/] },
      "event_window": [7.5, 14.0],
      "granular": { "C2-3": [/*…*/], "N4": [/*…*/] }
    }
  ]
}
```

Populate `configs` from the tuning step: each evaluated candidate (mark the
non-dominated ones `pareto: true`), with its objective vector from the reward
terms, `$` cost, derived `savings`, and the macro/granular series from re-running
that configuration. The baseline is the un-optimized run.

The dashboard is a multi-tab suite. The tabs beyond *Trade-offs* use these
additional fields (attach `control` and `ensemble` at least to the Pareto
configs; other configs degrade gracefully):

```jsonc
{
  // Cost-benefit tab — per config
  "economics": { "capital": 0, "om_annual": 0, "benefit_annual": 0,
                 "damage_avoided": 0, "cso_penalty_avoided": 0, "energy_saved": 0,
                 "payback_years": 0.0, "npv": 0 },

  // Control tab — per config (explainability of the market controller)
  "control": {
    "times": [/* = DATA.times */],
    "routes": [ { "id": "GATE_…", "setting": [0..1],
                  "price_buyer": [0..1], "price_seller": [0..1] } ],
    "heat_assets": ["C2-3", ...],
    "heat": [ [/* price per time for that asset */], ... ]   // asset × time
  },

  // Robustness tab — per config, plus DATA.baseline.ensemble
  "ensemble": { "storms": ["10-yr", ...],
                "flood": [/* per storm */], "cso": [/* per storm */],
                "reliability_pct": 0.0 }
}
```

Parallel coordinates (multi-objective tab) is computed from each config's
`objectives` + `savings.cost`; selecting a Pareto point isolates its line.
