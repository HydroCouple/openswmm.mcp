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
    NOT_SUPPORTED: str = "NOT_SUPPORTED"
    STALE_OBJECT: str = "STALE_OBJECT"


# ---------------------------------------------------------------------------
# ToolError import (fastmcp may or may not be installed)
# ---------------------------------------------------------------------------
# Prefer ``fastmcp.exceptions.ToolError`` since modern fastmcp versions only
# expose it there; fall back to ``fastmcp.ToolError`` (older releases) and
# finally to a local stand-in when fastmcp isn't installed at all (e.g.
# during static analysis).  Keeping a single canonical class lets tools and
# tests use ``isinstance(..., ToolError)`` reliably.

try:
    from fastmcp.exceptions import ToolError  # noqa: F401
except ImportError:  # pragma: no cover
    try:
        from fastmcp import ToolError  # noqa: F401
    except ImportError:

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


# ---------------------------------------------------------------------------
# StaleObjectError translation
# ---------------------------------------------------------------------------
#
# v1 wrappers (``Node``, ``Link``, etc.) are tied to a generation counter on
# the Solver.  Mutations like ``delete_object`` or ``rename_*`` bump the
# generation, after which any wrapper minted earlier raises
# ``StaleObjectError`` when its properties are accessed.  Tools that hand a
# wrapper to a worker thread can hit this in the middle of an
# ``asyncio.to_thread`` callable.  Without translation, the engine
# exception escapes as a 500-shaped failure.  Wrap the engine surface with
# this helper to surface a clean ``ToolError`` instead.

try:
    from openswmm.engine import StaleObjectError as _EngineStaleObjectError
except ImportError:  # pragma: no cover
    _EngineStaleObjectError = None  # type: ignore[assignment]


def translate_stale_object(exc: BaseException) -> ToolError | None:
    """Return a ``ToolError`` if *exc* is an engine StaleObjectError, else ``None``.

    Lets tool code do::

        try:
            value = await asyncio.to_thread(lambda: session.nodes[idx].depth)
        except Exception as e:
            translated = translate_stale_object(e)
            if translated is not None:
                raise translated
            raise

    Or use :func:`raise_stale_object_as_tool_error` as a one-liner
    re-raise inside a focused try/except.
    """
    if _EngineStaleObjectError is None:
        return None
    if not isinstance(exc, _EngineStaleObjectError):
        return None
    return ToolError(
        f"[{ErrorCode.STALE_OBJECT}] {exc} "
        "Re-look up the element from session.nodes / session.links / etc."
    )


def raise_stale_object_as_tool_error(exc: BaseException) -> None:
    """Raise a translated ToolError when *exc* is a StaleObjectError.

    Helper for tool authors who want a single-line re-raise inside a
    narrow ``try/except StaleObjectError`` (or a broad ``except
    Exception``) clause.  Returns normally — i.e. is a no-op — when the
    exception is not a stale-object error, so the caller can ``raise`` the
    original after this returns.
    """
    translated = translate_stale_object(exc)
    if translated is not None:
        raise translated from exc
