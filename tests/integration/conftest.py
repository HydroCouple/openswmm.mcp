"""Pytest configuration for the integration test suite.

Integration tests require a working compiled ``openswmm`` engine and are
**skipped by default**.  They only run when explicitly requested via:

- the ``--run-integration`` CLI flag, or
- the ``OPENSWMM_RUN_INTEGRATION`` environment variable (any truthy value).

Usage examples::

    # Skip integration tests (default)
    pytest

    # Run everything including integration tests
    pytest --run-integration

    # Enable via environment variable (handy in CI)
    OPENSWMM_RUN_INTEGRATION=1 pytest
"""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--run-integration`` CLI flag."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require the compiled openswmm engine.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests marked ``integration`` unless the user opted in."""
    run_integration = config.getoption("--run-integration", default=False) or os.environ.get(
        "OPENSWMM_RUN_INTEGRATION"
    )

    if run_integration:
        # User asked for integration tests -- nothing to skip.
        return

    skip_marker = pytest.mark.skip(
        reason=(
            "Integration tests are disabled by default.  "
            "Pass --run-integration or set OPENSWMM_RUN_INTEGRATION=1 to enable."
        )
    )

    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
