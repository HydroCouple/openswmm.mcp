# Resources

The OpenSWMM MCP Server exposes model data through nine MCP resources using
the `swmm://` URI scheme. Resources provide structured, read-only access to
model metadata and simulation results without invoking tools.

## URI Scheme

All resource URIs follow the pattern:

```
swmm://sessions
swmm://session/{session_id}/...
```

Where `{session_id}` is the identifier of an active simulation session (e.g.
`"default"`).

## Available Resources

### `swmm://sessions`

List all active simulation sessions.

**Returns:** JSON array of session metadata objects, each containing:
- `session_id` -- session identifier
- `state` -- current lifecycle state
- `working_dir` -- session working directory

**Example:**
```json
[
  {
    "session_id": "default",
    "state": "ended",
    "working_dir": "/tmp/openswmm/default"
  }
]
```

---

### `swmm://session/{session_id}/summary`

Model summary for the specified session.

**Returns:** JSON object with:
- `session_id`, `state`
- `node_count`, `link_count`, `subcatchment_count`
- `flow_units`, `route_model`
- `start_time`, `end_time`, `routing_step`

---

### `swmm://session/{session_id}/nodes`

List all nodes in the model.

**Returns:** JSON array of objects, each with `node_id`, `type`, and `index`.

---

### `swmm://session/{session_id}/nodes/{node_id}`

Detailed properties and runtime state for a single node.

**Returns:** JSON object with:
- `node_id`, `index`, `node_type`
- `invert_elev`, `max_depth`
- Runtime state (when simulation has run): `depth`, `head`, `volume`,
  `lateral_inflow`, `overflow`

---

### `swmm://session/{session_id}/links`

List all links in the model.

**Returns:** JSON array of objects, each with `link_id`, `type`, and `index`.

---

### `swmm://session/{session_id}/links/{link_id}`

Detailed properties and runtime state for a single link.

**Returns:** JSON object with:
- `link_id`, `index`, `link_type`
- `from_node`, `to_node`, `length`, `roughness`
- Runtime state (when simulation has run): `flow`, `depth`, `velocity`,
  `capacity`

---

### `swmm://session/{session_id}/subcatchments`

List all subcatchments in the model.

**Returns:** JSON array of objects, each with `subcatch_id` and `index`.

---

### `swmm://session/{session_id}/mass_balance`

Continuity errors and volumetric totals for the simulation.

**Returns:** JSON object with:
- `runoff_continuity_error`, `routing_continuity_error`
- `quality_continuity_error` (may be `null`)
- `runoff_total` -- breakdown of runoff volume components
- `routing_total` -- breakdown of routing volume components

---

### `swmm://session/{session_id}/options`

The model's simulation options.

**Returns:** JSON object with:
- `flow_units`, `route_model`
- `start_time`, `end_time`, `routing_step`

## Using Resources

Resources are accessed through the MCP `read_resource` method. In Claude Code,
the LLM reads resources automatically when guided by prompts or when it
needs structured model data.

Resources complement tools: where tools perform actions and return results,
resources provide passive data access. For example, a prompt might instruct
the LLM to "read `swmm://session/default/summary`" to understand the model
before deciding which analysis tools to invoke.
