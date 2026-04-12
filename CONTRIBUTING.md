# Contributing to OpenSWMM MCP Server

Thank you for your interest in contributing to the OpenSWMM MCP Server. This document explains how to get started, the development workflow, and what we expect from contributions.

## Code of Conduct

All participants in this project are expected to follow our [Code of Conduct](CODE_OF_CONDUCT.md). Please read it before contributing.

## Getting Started

### Prerequisites

- Python 3.10 or later
- Git

### Development Setup

```bash
git clone https://github.com/HydroCouple/openswmm.mcp.git
cd openswmm.mcp
pip install -e ".[dev,docs]"
```

This installs the package in editable mode along with all development and documentation dependencies (pytest, ruff, sphinx, etc.).

## Development Workflow

### 1. Create a Branch

Create a feature or fix branch from `main`:

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-fix-description
```

### 2. Make Your Changes

- Follow the existing code style (enforced by ruff)
- Add or update tests for any new or changed functionality
- Keep commits focused and atomic

### 3. Run Quality Checks

Before submitting, make sure all checks pass:

```bash
# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Unit tests
pytest tests/unit/ -v

# Unit tests with coverage
pytest tests/unit/ --cov=openswmm_mcp --cov-report=term-missing
```

Integration tests require the compiled OpenSWMM engine and are optional for most contributions:

```bash
OPENSWMM_RUN_INTEGRATION=1 pytest tests/integration/ -v
```

### 4. Build Documentation (if applicable)

If your changes affect the public API or user-facing behavior:

```bash
sphinx-build -b html docs docs/_build/html
```

### 5. Submit a Pull Request

- Push your branch and open a pull request against `main`
- Provide a clear description of what your changes do and why
- Reference any related issues (e.g., "Fixes #42")
- Ensure CI checks pass

## Project Structure

```
src/openswmm_mcp/
    __init__.py          # Package version
    __main__.py          # CLI entry point
    server.py            # Root FastMCP server + mount composition
    auth.py              # OAuth/JWT authentication providers
    config.py            # pydantic-settings configuration
    session.py           # SimSession + SessionManager
    models.py            # Pydantic response models
    errors.py            # Error codes and helpers
    dependencies.py      # Lifespan and dependency injection
    tools/               # 7 tool sub-servers (lifecycle, query, forcing, etc.)
    resources/           # swmm:// URI resource handlers
    prompts/             # Guided workflow prompt templates
    _util/               # Internal formatting and validation helpers
tests/
    conftest.py          # Shared fixtures and mock engine injection
    mocks/engine.py      # Mock classes for openswmm.engine API
    unit/                # Unit tests (no compiled engine required)
    integration/         # Integration tests (requires real engine)
```

## Conventions

### Code Style

- **Formatter/linter:** [Ruff](https://docs.astral.sh/ruff/) (configuration in `pyproject.toml`)
- **Target Python:** 3.10+
- **Type hints:** Use type annotations for all public function signatures
- **Docstrings:** Google-style docstrings for public modules, classes, and functions

### Testing

- All new tools, resources, and prompts must have corresponding unit tests
- Unit tests use the mock engine in `tests/mocks/engine.py` -- no compiled C extensions needed
- Use `pytest-asyncio` for async test functions (auto mode is configured)
- Test files follow the naming convention `test_<module>.py`

### Tool Development

Each tool domain lives in its own sub-server under `src/openswmm_mcp/tools/`. To add a new tool:

1. Add the tool function with `@sub_mcp.tool` decorator in the appropriate module
2. Use Pydantic models from `models.py` for structured responses
3. Wrap synchronous engine calls in `asyncio.to_thread()`
4. Add corresponding mock behavior in `tests/mocks/engine.py` if needed
5. Write unit tests covering success paths and error cases

### Commit Messages

- Use clear, descriptive commit messages
- Start with a verb in imperative mood (e.g., "Add flooding summary tool", "Fix session cleanup race condition")
- Reference issues where applicable

## Reporting Issues

- Use [GitHub Issues](https://github.com/HydroCouple/openswmm.mcp/issues) to report bugs or request features
- Include steps to reproduce for bugs
- Include your Python version, OS, and relevant dependency versions

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
