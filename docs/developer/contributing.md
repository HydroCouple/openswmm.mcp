# Contributing

Thank you for your interest in contributing to the OpenSWMM MCP Server. This
guide covers development setup, coding standards, and the pull request
process.

## Development Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/HydroCouple/openswmm.mcp.git
   cd openswmm.mcp
   ```

2. **Create a virtual environment:**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   .venv\Scripts\activate     # Windows
   ```

3. **Install in editable mode with dev extras:**

   ```bash
   pip install -e ".[dev,docs]"
   ```

4. **Verify the installation:**

   ```bash
   pytest tests/unit/ -v
   ruff check src/ tests/
   ruff format --check src/ tests/
   ```

## Project Layout

```
src/openswmm_mcp/    # Main package (src layout)
tests/
  unit/              # Unit tests (mock engine, no C extensions needed)
  integration/       # Integration tests (require compiled openswmm engine)
  mocks/             # Mock engine classes
docs/                # Sphinx documentation
```

## Coding Standards

### Style

- **Formatter:** [Ruff](https://docs.astral.sh/ruff/) (`ruff format`).
- **Linter:** Ruff (`ruff check`) with rules: `E`, `F`, `I`, `W`, `UP`.
- **Line length:** 100 characters.
- **Target Python:** 3.10+.

### Type Annotations

- All public functions and methods should have type annotations.
- Use `from __future__ import annotations` for PEP 604 union syntax (`X | Y`).
- Pydantic models define the schema for all tool responses.

### Docstrings

- Use NumPy-style docstrings for tool functions (these are rendered by
  Napoleon in Sphinx and surfaced to MCP clients as tool descriptions).
- Module-level docstrings describe the purpose of each file.

### Async Conventions

- All tool functions are `async`.
- Engine calls (synchronous Cython bindings) are wrapped in
  `asyncio.to_thread()`.
- Use `asyncio.Lock` for shared state protection in `SessionManager`.

## Adding a New Tool

1. Identify the appropriate sub-server module in `src/openswmm_mcp/tools/`.
2. Define the tool function with `@sub_mcp.tool()` decorator.
3. Add parameter type annotations -- FastMCP generates the JSON schema from
   these.
4. Use `get_session_manager(ctx)` and `require_state()` for session access.
5. Wrap engine calls in `asyncio.to_thread()`.
6. Return a Pydantic model or dict.
7. Add a corresponding unit test in `tests/unit/`.
8. Update the documentation in `docs/user-guide/tools.md`.

## Running Tests

### Unit Tests

Unit tests use mock engine classes and the in-memory MCP client. They do not
require the compiled `openswmm` engine.

```bash
pytest tests/unit/ -v
```

### With Coverage

```bash
pytest tests/unit/ -v --cov=openswmm_mcp --cov-report=term-missing
```

### Integration Tests

Integration tests require a compiled `openswmm` engine installation. They
are marked with `@pytest.mark.integration` and skipped by default.

```bash
OPENSWMM_RUN_INTEGRATION=1 pytest tests/integration/ -v
```

## Linting

```bash
ruff check src/ tests/
ruff format --check src/ tests/
```

To auto-fix:

```bash
ruff check --fix src/ tests/
ruff format src/ tests/
```

## Building Documentation

```bash
sphinx-build -W -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` in a browser to preview.

## Pull Request Process

1. **Branch from `main`:** Create a feature branch with a descriptive name
   (e.g. `feature/add-pump-tools`, `fix/session-cleanup`).

2. **Write tests first:** Ensure new functionality has corresponding unit
   tests. Aim for 80 %+ coverage on new code.

3. **Run CI checks locally:**

   ```bash
   ruff check src/ tests/
   ruff format --check src/ tests/
   pytest tests/unit/ -v --cov=openswmm_mcp
   sphinx-build -W -b html docs docs/_build/html
   ```

4. **Commit with clear messages:** Use imperative mood
   (e.g. "Add pump speed forcing tool").

5. **Open a pull request:** Target `main`. Include a summary of changes and
   a test plan.

6. **CI must pass:** The GitHub Actions CI workflow runs linting, tests across
   Python 3.10--3.13 on Ubuntu/macOS/Windows, and the documentation build.

7. **Review:** At least one maintainer approval is required before merging.

## Reporting Issues

Open an issue on
[GitHub](https://github.com/HydroCouple/openswmm.mcp/issues) with:

- A clear description of the problem or feature request.
- Steps to reproduce (for bugs).
- Python version and operating system.
- Relevant logs or error messages.
