<p align="center">
  <img src="images/hydrocouplecomposer.png" alt="OpenSWMM MCP" width="120">
</p>

# openswmm-mcp

![PyPI](https://img.shields.io/pypi/v/openswmm-mcp)
![Python](https://img.shields.io/pypi/pyversions/openswmm-mcp)
![CI](https://github.com/HydroCouple/openswmm.mcp/actions/workflows/ci.yml/badge.svg)
![Docs](https://github.com/HydroCouple/openswmm.mcp/actions/workflows/docs.yml/badge.svg)
![License](https://img.shields.io/pypi/l/openswmm-mcp)

## Overview

**openswmm-mcp** is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that wraps the [OpenSWMM](https://github.com/HydroCouple/OpenSWMMCore) stormwater engine (v6.0), built with [FastMCP 3.x](https://gofastmcp.com/). It enables LLM clients -- such as Claude, ChatGPT, or any MCP-compatible agent -- to interact with the full SWMM simulation lifecycle through natural language:

- **Build** SWMM models programmatically from scratch
- **Run** simulations with progress reporting
- **Query** nodes, links, subcatchments, and gages in real time
- **Apply** runtime forcing overrides and control rules for what-if scenarios
- **Analyze** flooding, pipe capacity, mass balance, and time-series output
- **Branch** simulations via hot-start save/load and session cloning
- **Export** results to CSV or JSON for downstream analysis

## Features

The server exposes **14 core tools**, **5 optional gym tools** and an opt-in
`run_python` tool. Together their definitions take about 17k characters (under
5k tokens), so the server loads in any MCP client, including Claude Desktop.
v1 registered 663 tools and could not.

Five generic tools (`describe`, `find`, `get`, `set`, `call`) reach every
property and method of the engine through its machine-readable catalog
(`openswmm.engine.catalog`). New engine capabilities reach the server without
new tool code.

- **Sessions and runs** -- `open_model`, `run`, `session`, `save`
- **Catalog access** -- `describe`, `find`, `get`, `set`, `call`, `edit`
- **Results** -- `timeseries`, `report`, `compare`, `export`
- **Gym** (`OPENSWMM_MCP_TOOLSETS=core,gym`) -- `gym_describe`, `gym_config`,
  `gym_env`, `gym_job`, `gym_score`

See the [tools guide](docs/user-guide/tools.md) for details and the v1-to-v2
mapping.

### Additional Capabilities

- **Resources** for the engine catalog (`swmm://catalog`, `swmm://catalog/{target}`),
  sessions and bundled skills
- **7 prompt templates** and **3 bundled skills** (capacity assessment,
  calibration, operational optimisation)
- **OAuth/JWT authentication** for HTTP transport via `fastmcp[auth]`
- **Progress reporting** for long runs and **multi-session management**

## Installation

```bash
pip install openswmm-mcp
```

Authentication support is included by default through the `fastmcp[auth]` dependency. To install with development and documentation extras:

```bash
pip install "openswmm-mcp[dev,docs]"
```

The gym tools need the `gym` extra (`pip install "openswmm-mcp[gym]"`) and
`OPENSWMM_MCP_TOOLSETS=core,gym`.

### Requirements

- Python 3.10+
- `openswmm >= 6.0.0a4.dev1` built with `openswmm.engine.catalog` (the OpenSWMM engine Python bindings)
- `fastmcp >= 3.2, < 4`
- `pydantic >= 2.0`
- `numpy >= 1.21`

## Quick Start

### With Claude Code

Add the following to your MCP configuration (e.g. `~/.claude/settings.json` or `.mcp.json`):

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "python",
      "args": ["-m", "openswmm_mcp"],
      "env": {
        "OPENSWMM_MCP_WORKING_DIR": "/path/to/models"
      }
    }
  }
}
```

### With Claude Desktop

Add the server to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`),
using the full path of the Python environment where the server is installed:

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "openswmm_mcp"],
      "env": {
        "OPENSWMM_MCP_WORKING_DIR": "/path/to/models"
      }
    }
  }
}
```

### With uv

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "uv",
      "args": ["run", "--with", "openswmm.mcp", "openswmm.mcp"]
    }
  }
}
```

### Example Conversation

```
User:  Open the model at /data/site_drainage.inp and run the simulation.

Claude: [open_model] 12 nodes, 11 links, 7 subcatchments, CFS units, DYNWAVE routing.
        [run] Finished: 3,164 steps, routing continuity error 0.02 %.

User:  Which nodes flooded?

Claude: [report(name="flooding")] 6 nodes flooded; J10 worst: 231,937 ft3 over 29 min.

User:  What if we upsize C10, the conduit leaving J10, to 3 ft?

Claude: [open_model(session_id="upsize")]
        [set(kind="link.xsect", changes=[{"id": "C10", "field": "g1", "value": 3.0}])]
        [run] [compare(a="default", b="upsize", kind="node", variable="depth")]
        Reports the change in peak depth and flood volume at each node.
```

## Available Resources

| URI | Contents |
|-----|----------|
| `swmm://catalog` | Every engine target with its class and description |
| `swmm://catalog/{target}` | Fields and methods of one target |
| `swmm://sessions` | Open sessions |
| `swmm://session/{id}/summary` | Counts, options, time window and files of one session |
| `swmm://skills`, `swmm://skills/{name}` | Bundled skills |

## Available Prompts

| Prompt | Description | Parameters |
|--------|-------------|------------|
| `analyze_model` | Comprehensive model review and assessment | `inp_path` |
| `diagnose_flooding` | Investigate flooding causes and suggest mitigations | `session_id`, `node_ids` (optional, comma-separated) |
| `compare_scenarios` | Side-by-side comparison of two simulation sessions | `session_a`, `session_b` |
| `design_review` | Check model against design standards | `session_id`, `standard` (optional) |
| `what_if` | Set up and evaluate a what-if scenario | `session_id`, `description` |
| `build_simple_model` | Guided construction of a model from a text description | `description` |
| `explain_results` | Plain-language result explanation for stakeholders | `session_id` |
| `use_skill` | Load a bundled skill's instructions | `skill_name` |

## Configuration

All settings are controlled via environment variables with the `OPENSWMM_MCP_` prefix:

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `OPENSWMM_MCP_WORKING_DIR` | `"./"` | Base directory for model files and session data |
| `OPENSWMM_MCP_MAX_SESSIONS` | `5` | Maximum number of concurrent simulation sessions |
| `OPENSWMM_MCP_TRANSPORT` | `"stdio"` | Transport protocol: `stdio`, `http`, or `sse` |
| `OPENSWMM_MCP_HTTP_PORT` | `8080` | Port for HTTP/SSE transport |
| `OPENSWMM_MCP_LOG_LEVEL` | `"INFO"` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `OPENSWMM_MCP_TOOLSETS` | `"core"` | Tool sets to register: `core`, or `core,gym` for the gym tools |
| `OPENSWMM_MCP_ENABLE_PYTHON` | `false` | Register `run_python` (stdio transport only; runs code with the server's permissions) |
| `OPENSWMM_MCP_OAUTH_ISSUER` | `None` | OAuth issuer URL for JWT validation |
| `OPENSWMM_MCP_OAUTH_AUDIENCE` | `None` | Expected OAuth audience claim |
| `OPENSWMM_MCP_JWT_JWKS_URL` | `None` | JWKS endpoint URL for JWT signature verification |

## HTTP Transport with Authentication

To run the server over HTTP with OAuth/JWT authentication:

```bash
OPENSWMM_MCP_TRANSPORT=http \
OPENSWMM_MCP_HTTP_PORT=8000 \
OPENSWMM_MCP_OAUTH_ISSUER=https://auth.example.com \
OPENSWMM_MCP_OAUTH_AUDIENCE=openswmm-mcp \
OPENSWMM_MCP_JWT_JWKS_URL=https://auth.example.com/.well-known/jwks.json \
python -m openswmm_mcp
```

For unauthenticated HTTP (development only):

```bash
OPENSWMM_MCP_TRANSPORT=http OPENSWMM_MCP_HTTP_PORT=8000 python -m openswmm_mcp
```

## Development

### Setup

```bash
git clone https://github.com/HydroCouple/openswmm.mcp.git
cd openswmm.mcp
pip install -e ".[dev,docs]"
```

### Running Tests

```bash
pytest tests/unit/ -v                        # requires the compiled engine
pytest tests/unit/ --cov=openswmm_mcp         # with coverage
```

### Linting

```bash
ruff check src/ tests/
ruff format src/ tests/
```

### Building Docs

```bash
sphinx-build -b html docs docs/_build/html
```

The documentation site uses [PyData Sphinx Theme](https://pydata-sphinx-theme.readthedocs.io/) with [MyST Parser](https://myst-parser.readthedocs.io/) for Markdown support.

## Architecture

```
                          MCP Client (Claude, etc.)
                                    |
                              MCP Protocol
                                    |
                        +-----------+-----------+
                        |  FastMCP Root Server  |
                        |  (openswmm-mcp)       |
                        +-----------+-----------+
                                    |
          +-------+-------+--------+--------+---------+-------+--------+--------+
          |       |       |        |        |         |       |        |        |
     lifecycle  query  forcing  analysis  building editing hotstart spatial
       (mcp)   (mcp)   (mcp)    (mcp)     (mcp)    (mcp)   (mcp)    (mcp)
          |       |       |        |        |         |       |        |        |
          +-------+-------+--------+--------+---------+-------+--------+--------+
                                    |
                           SessionManager
                          /      |       \
                     Session  Session  Session ...
                        |
               +--------+--------+
               |        |        |
            Solver   Nodes    Links   ...
               |
         openswmm.engine (C++ bindings)
```

Each tool namespace is a separate `FastMCP` sub-server, mounted on the root server with a namespace prefix. The `SessionManager` manages concurrent simulation sessions, each wrapping an `openswmm.engine.Solver` instance and its associated domain accessors (nodes, links, subcatchments, gages, pollutants, mass balance, statistics, forcing, controls, spatial, quality, hot-start, and infrastructure).

Resources and prompts are mounted without a namespace prefix to keep their URIs concise.

## Contributing

Contributions are welcome! Please read our [Contributing Guide](CONTRIBUTING.md) for details on the development workflow, code style, and how to submit pull requests.

This project follows the [Contributor Covenant 3.0 Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

In brief:

1. Fork the repository and create a feature branch.
2. Install development dependencies: `pip install -e ".[dev,docs]"`
3. Make your changes and add or update tests.
4. Ensure `ruff check` and `ruff format` pass with no issues.
5. Ensure `pytest tests/unit/` passes.
6. Open a pull request against `main` with a clear description of your changes.

## Authors

See [AUTHORS.md](AUTHORS.md) for the full list of contributors to this project.

## License

Apache License, Version 2.0 -- see [LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for required attribution.

## Acknowledgements

- [OpenSWMM Engine](https://github.com/HydroCouple/OpenSWMMCore) -- the next-generation SWMM computational engine
- [FastMCP](https://gofastmcp.com/) -- the Python framework for building MCP servers
- [US EPA SWMM](https://www.epa.gov/water-research/storm-water-management-model-swmm) -- the original Storm Water Management Model on which OpenSWMM is based
