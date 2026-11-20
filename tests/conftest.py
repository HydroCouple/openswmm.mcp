"""Shared configuration for the openswmm.mcp test suite.

Every test drives the real, compiled ``openswmm.engine``: it is imported here
unconditionally, so an environment without it fails at collection instead of
skipping the suite and reporting green.
"""

import openswmm.engine  # noqa: F401
