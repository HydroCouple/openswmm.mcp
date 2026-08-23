# Capacity dashboard — data contract

`capacity_dashboard.template.html` is data-driven. Build the final dashboard by
replacing two placeholders in the template:

- `__PLOTLY_JS__` — inline the contents of `plotly.min.js` (offline, self
  contained) or swap that whole `<script>` for a CDN tag.
- `__DATA_JSON__` — `json.dumps(DATA)` where `DATA` matches the shape below.

All `util` / `avail` / global series are arrays aligned to `times` (same length).
`util` is utilization in `[0, ~1.2]` (hmax/Hmax for conduits, fill fraction for
storage, depth/rim for junctions); `avail` is the fractional spare in `[0, 1]`.

```jsonc
{
  "times": [0, 0.25, ...],                       // x-axis, hours or step index
  "event_windows": [[6.75, 16.0]],               // adverse windows, in `times` units
  "global_state": {
    "flooding":     [/* len = times */],         // system rate
    "uncontrolled": [/* len = times */]          // untreated-outfall discharge rate
  },
  "nodes": [
    { "id": "N4", "x": 3.1, "y": 5.2, "kind": "JUNCTION|STORAGE|OUTFALL",
      "commodity": "conveyance|storage|treatment",
      "util": [/* len = times */], "avail": [/* len = times */] }
  ],
  "links": [
    { "id": "C2-3", "x0": 1, "y0": 2, "x1": 3, "y1": 2, "kind": "conduit",
      "util": [/* hmax/Hmax over time */], "avail": [/* len = times */] }
  ],
  "available": {                                 // system-wide available capacity (%), optional
    "conveyance": [/* len = times */],
    "storage":    [/* len = times */],
    "treatment":  [/* len = times */]
  },
  "meta": {
    "util_label": "utilization (hmax/Hmax or fill fraction)",
    "constrained_threshold": 0.9,
    "surcharge_threshold": 1.0,
    "min_velocity": 0.6, "max_velocity": 3.0    // self-cleansing / scour limits
  }
}
```

The dashboard is a multi-tab suite; the tabs beyond *Explore* use these extra
fields (each node/link also carries `vel` (velocity series, len = times) and
`consequence` (1–5) for the velocity and risk tabs):

```jsonc
{
  // HGL profiles tab — animated hydraulic grade line per path
  "paths": [
    { "id": "...", "name": "Trunk main", "nodes": ["N0","N1",...],
      "dist": [/* chainage */], "invert": [...], "crown": [...], "rim": [...],
      "hgl_t": [ [/* HGL at each node */], ...  /* one inner array per time */ ] }
  ],

  // Volume & mass tab — Sankey of system routing
  "sankey": { "labels": [...], "source": [int], "target": [int], "value": [num],
              "continuity_error_pct": 1.7, "note": "..." },

  // Risk & KPIs tab — level-of-service scorecard
  "kpis": [ { "name": "...", "value": 0, "target": 0, "unit": "nodes", "pass_": true } ],
  // (criticality ranking is computed client-side from util × duration × consequence)

  // Recommendations tab — auto-generated infrastructure improvements
  "recommendations": [
    { "type": "Conduit upsizing|Parallel relief|Storage / detention|Green infrastructure (LID)|RTC vs capital",
      "asset": "...", "rationale": "which diagnostic triggered it",
      "sizing": "...", "benefit": "re-simulated impact", "cost_tier": "$|$$|$$$" }
  ]
}
```

The *Duration & velocity* tab needs no extra arrays — exceedance curves are
computed from each asset's `util`, and the velocity screening from `vel` vs the
`meta` velocity limits.

Source the series from the temporal step: per-element `analysis_get_time_series`
for `util`, and the system-wide available-capacity series for context. Coordinates
come from `spatial_get_all_coordinates` / `spatial_get_all_vertices`.
