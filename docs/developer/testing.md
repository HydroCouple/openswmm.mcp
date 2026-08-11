# Testing

The OpenSWMM MCP Server uses [pytest](https://docs.pytest.org/) with
`pytest-asyncio` for its test suite. Tests are divided into unit tests
(which use a mock engine) and integration tests (which require the compiled
`openswmm` engine).

## Test Layout

```
tests/
  __init__.py
  conftest.py                       # Shared fixtures, mock patching, MCP client
  data/                             # Sample .inp files
  unit/
    __init__.py
    conftest.py                     # Unit-level fixtures
    data/                           # Unit-test fixtures
    test_session.py                 # SessionManager create/get/close/list
    test_lifecycle.py               # open_model, run_simulation, step, close
    test_query.py                   # get_node/link/subcatch/gage_info, find_elements
    test_forcing.py                 # set_forcing, clear_forcing, state guards
    test_controls.py                # control rules
    test_analysis.py                # statistics, mass balance, time series
    test_analysis_output_reader.py  # output-reader breadth (snapshot/series/attr)
    test_building.py                # create_model, add_node/link/subcatch, validate
    test_editing.py                 # cascade-delete, type conversion, renames
    test_nodes.py                   # nodes_* fine-grained accessors
    test_links.py                   # links_* fine-grained accessors
    test_subcatchments.py           # subcatchments_* fine-grained accessors
    test_inflows.py                 # external / DWF / RDII / hydrographs
    test_infrastructure.py          # transects / streets / inlets / LIDs
    test_tables.py                  # time series / curves / patterns
    test_hotstart.py                # save/load hotstart, clone, saves_*
    test_spatial_quality.py         # coordinates, quality, treatment, geometry
    test_geopackage_tools.py        # geopackage_* tools
    test_twod.py                    # twod_* 2D-surface tools (twod_parking_lot.inp fixture)
    test_model_userflags_schema.py  # model_userflag_* schema/value + model_file_path_* tools
    test_forcing_climate.py         # forcing_get_climate_evap_rate
    test_backend_dispatch.py        # openswmm vs legacy backend selection
    test_phase2_wave2.py            # Phase-2 wave-2 regression set
    test_resources.py               # All 9 swmm:// resources
    test_prompts.py                 # All 7 prompts
    test_auth.py                    # OAuth, JWT, MultiAuth, stdio bypass
  integration/
    __init__.py
    conftest.py                     # Real engine fixtures, --run-integration gate
    test_end_to_end.py              # Full open -> run -> query -> close
```

## Mock Backend Pattern

Unit tests avoid depending on the compiled `openswmm` C extensions by
swapping the engine-agnostic `Backend` for an in-memory fake.  The
shared fixtures live in `tests/conftest.py` and the unit-level
`tests/unit/conftest.py`; together they hand each test an MCP `Client`
wired to a server whose backend is a controllable state machine
returning deterministic data for a small synthetic network.

Tests should depend on the **backend abstraction** rather than on
specific engine classes (`Solver`, `Nodes`, …) — that way they
exercise the same code path for both the OpenSWMM v6 and legacy
backends.  See `test_backend_dispatch.py` for the dual-backend
coverage pattern.

## In-Memory MCP Client

FastMCP provides a `Client(transport=mcp)` that connects directly to the
server object in memory, without requiring a subprocess or network socket.
This makes tests fast and deterministic.

```python
from fastmcp import Client

@pytest.fixture
async def mcp_client(mock_engine):
    from openswmm_mcp.server import mcp
    async with Client(transport=mcp) as client:
        yield client
```

Tools are invoked via:

```python
result = await client.call_tool("lifecycle_open_model", {
    "inp_path": "/tmp/test.inp",
    "session_id": "test",
})
```

## Running Unit Tests

```bash
# Basic run
pytest tests/unit/ -v

# With coverage
pytest tests/unit/ -v --cov=openswmm_mcp --cov-report=term-missing

# Single test file
pytest tests/unit/test_lifecycle.py -v

# Single test function
pytest tests/unit/test_lifecycle.py::test_open_model_success -v
```

## Running Integration Tests

Integration tests require a working `openswmm` engine installation and
are **skipped by default**.  The integration suite has its own
`tests/integration/conftest.py` that gates collection on either a
CLI flag or an environment variable — whichever is more convenient.

CLI flag:

```bash
pytest --run-integration tests/integration/ -v
```

Environment variable (any truthy value):

```bash
OPENSWMM_RUN_INTEGRATION=1 pytest tests/integration/ -v
```

Without either of these, the integration tests are collected as
*skipped* with a clear message: *"Pass --run-integration or set
OPENSWMM_RUN_INTEGRATION=1 to enable."*

Integration tests use the bundled SWMM example inputs and exercise the
real engine through a full open-step-query-run-close cycle.

## Test Configuration

The `pyproject.toml` configures pytest:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"
```

`asyncio_mode = "auto"` is set in the project's `pytest-asyncio`
configuration; `pytest-asyncio` is a hard `[dev]` dependency.

## Writing New Tests

1. **Unit tests** go in `tests/unit/test_<module>.py`.
2. Use the `mcp_client` fixture for tool invocations.
3. Use `mock_engine` for engine isolation.
4. Assert on the tool result's content (parsed from the MCP response).
5. Test both success and error paths (invalid state, missing elements, etc.).

Example:

```python
async def test_close_model(mcp_client):
    # First open a model
    await mcp_client.call_tool("lifecycle_open_model", {
        "inp_path": "/tmp/test.inp",
    })

    # Close it
    result = await mcp_client.call_tool("lifecycle_close_model", {})
    assert result["status"] == "closed"
```

## Coverage Target

The project targets 80 %+ code coverage on the `openswmm_mcp` package.
Coverage is tracked via `pytest-cov` and uploaded to Codecov in CI.
