# Testing

The OpenSWMM MCP Server uses [pytest](https://docs.pytest.org/) with
`pytest-asyncio` for its test suite. Tests are divided into unit tests
(which use a mock engine) and integration tests (which require the compiled
`openswmm` engine).

## Test Layout

```
tests/
  __init__.py
  conftest.py              # Shared fixtures, mock patching, MCP client
  mocks/
    __init__.py
    engine.py              # Mock Solver, Nodes, Links, etc.
  unit/
    __init__.py
    test_session.py        # SessionManager create/get/close/list
    test_lifecycle.py      # open_model, run_simulation, step, close
    test_query.py          # get_node/link/subcatch/gage_info, find_elements
    test_forcing.py        # set_forcing, clear_forcing, state guards
    test_analysis.py       # statistics, mass_balance, time_series
    test_building.py       # create_model, add_node/link/subcatch, validate
    test_hotstart.py       # save/load hotstart, clone_session
    test_spatial_quality.py  # coordinates, quality, treatment
    test_resources.py      # All 9 swmm:// resources
    test_prompts.py        # All 7 prompts
    test_auth.py           # OAuth, JWT, MultiAuth, stdio bypass
  integration/
    __init__.py
    conftest.py            # Real engine fixtures
    test_end_to_end.py     # Full open -> run -> query -> close
```

## Mock Engine Pattern

Unit tests avoid depending on the compiled `openswmm` C extensions by using
mock classes that replicate the engine API surface.

### How Mocks Work

The `tests/mocks/engine.py` module provides mock classes:

- **`MockSolver`**: A controllable lifecycle state machine. Supports
  `open()`, `initialize()`, `start()`, `step()`, `end()`, and `close()`.
  Returns configurable step counts and timing.

- **`MockNodes`**, **`MockLinks`**, **`MockSubcatchments`**, **`MockGages`**:
  Return deterministic test data for a 12-element test network.

- **`MockMassBalance`**, **`MockStatistics`**, **`MockForcing`**,
  **`MockControls`**: Simulate post-simulation statistics and runtime forcing.

- **`MockOutputReader`**, **`MockModelBuilder`**, **`MockHotStart`**,
  **`MockSpatial`**, **`MockQuality`**: Cover analysis, building, and
  spatial/quality tool APIs.

### Patching Strategy

The `tests/conftest.py` file provides fixtures that monkeypatch the engine
imports in `openswmm_mcp.session`:

```python
@pytest.fixture
def mock_engine(monkeypatch):
    """Patch all openswmm.engine imports used by the session module."""
    monkeypatch.setattr("openswmm_mcp.session.Solver", MockSolver)
    monkeypatch.setattr("openswmm_mcp.session.Nodes", MockNodes)
    # ... etc.
```

This means unit tests never import or instantiate the real C extension
modules.

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

Integration tests require a working `openswmm` engine installation and are
skipped by default. Enable them with:

```bash
OPENSWMM_RUN_INTEGRATION=1 pytest tests/integration/ -v
```

Or using the pytest marker:

```bash
pytest -m integration -v
```

Integration tests use the `site_drainage_example.inp` file and exercise the
real engine through a full open-step-query-run-close cycle.

## Test Configuration

The `pyproject.toml` configures pytest:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"
asyncio_mode = "auto"
markers = [
    "integration: tests requiring compiled openswmm engine",
]
```

Key settings:

- `asyncio_mode = "auto"`: All `async def test_*` functions are
  automatically treated as async tests.
- `testpaths = ["tests"]`: pytest discovers tests in the `tests/` directory.
- The `integration` marker allows selective execution of integration tests.

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
