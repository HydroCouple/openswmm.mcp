"""Standardised error codes and helper utilities."""

from __future__ import annotations

import traceback
from typing import Any

# ---------------------------------------------------------------------------
# Error codes (string constants)
# ---------------------------------------------------------------------------


class ErrorCode:
    """Canonical error-code strings returned in tool responses."""

    SESSION_NOT_FOUND: str = "SESSION_NOT_FOUND"
    INVALID_STATE: str = "INVALID_STATE"
    ELEMENT_NOT_FOUND: str = "ELEMENT_NOT_FOUND"
    ENGINE_ERROR: str = "ENGINE_ERROR"
    VALIDATION_ERROR: str = "VALIDATION_ERROR"
    MAX_SESSIONS_REACHED: str = "MAX_SESSIONS_REACHED"


# ---------------------------------------------------------------------------
# ToolError import (fastmcp may or may not be installed)
# ---------------------------------------------------------------------------

try:
    from fastmcp import ToolError  # noqa: F401
except ImportError:  # pragma: no cover

    class ToolError(Exception):  # type: ignore[no-redef]
        """Lightweight stand-in when *fastmcp* is not available."""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def engine_error_response(exc: BaseException) -> dict[str, Any]:
    """Convert an arbitrary exception into a standardised error dict.

    Returns a dict suitable for returning directly from a tool handler::

        {
            "error": "ENGINE_ERROR",
            "message": "<str(exc)>",
            "detail": "<traceback lines>",
        }
    """
    return {
        "error": ErrorCode.ENGINE_ERROR,
        "message": str(exc),
        "detail": traceback.format_exception(type(exc), exc, exc.__traceback__),
    }
