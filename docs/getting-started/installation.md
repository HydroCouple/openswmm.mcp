# Installation

## From PyPI

The simplest way to install the OpenSWMM MCP Server is via pip:

```bash
pip install "openswmm.mcp[engine]"
```

This installs the server and its required runtime dependencies (`fastmcp`,
`pydantic`, `pydantic-settings`, `numpy`), plus the `openswmm` engine Python
bindings via the `[engine]` extra — `openswmm` is optional at the packaging
level (not every consumer needs the bindings installed locally), but nearly
every real deployment wants it, so it's the extra you almost always want.

## From Source

Clone the repository and install in editable mode:

```bash
git clone https://github.com/HydroCouple/openswmm.mcp.git
cd openswmm.mcp
pip install -e ".[dev]"
```

The `[dev]` extra includes testing and linting tools (`pytest`, `pytest-asyncio`,
`pytest-cov`, `ruff`).

## With uv

If you use [uv](https://github.com/astral-sh/uv) for fast Python package
management:

```bash
uv pip install openswmm.mcp
```

Or for development:

```bash
uv pip install -e ".[dev]"
```

## With Claude Code

The OpenSWMM MCP Server integrates directly with
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) as an MCP
server. Add the following to your `.claude/settings.json`:

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "openswmm.mcp",
      "args": [],
      "env": {
        "OPENSWMM_MCP_WORKING_DIR": "/path/to/your/models"
      }
    }
  }
}
```

Alternatively, if you installed with `uv` and want to use the `uvx` runner:

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "uvx",
      "args": ["openswmm.mcp"],
      "env": {
        "OPENSWMM_MCP_WORKING_DIR": "/path/to/your/models"
      }
    }
  }
}
```

## Requirements

- **Python**: 3.10 or later
- **OpenSWMM Engine**: `openswmm>=6.0.0a1` (the compiled C engine bindings
  must be available for simulation features; the server can start without them
  but tools that call the engine will fail)

## Verifying the Installation

After installing, verify that the server starts correctly:

```bash
python -m openswmm_mcp
```

This launches the server in stdio transport mode. Press `Ctrl+C` to stop it.

You can also check the installed version:

```bash
python -c "import openswmm_mcp; print(openswmm_mcp.__version__)"
```
