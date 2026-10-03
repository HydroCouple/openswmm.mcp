"""Standardised error codes and engine-exception translation."""

from __future__ import annotations

from fastmcp.exceptions import ToolError

__all__ = ["ErrorCode", "ToolError", "engine_error"]


class ErrorCode:
    """Canonical error-code strings prefixed to every tool error message."""

    SESSION_NOT_FOUND: str = "SESSION_NOT_FOUND"
    INVALID_STATE: str = "INVALID_STATE"
    ELEMENT_NOT_FOUND: str = "ELEMENT_NOT_FOUND"
    ENGINE_ERROR: str = "ENGINE_ERROR"
    VALIDATION_ERROR: str = "VALIDATION_ERROR"
    MAX_SESSIONS_REACHED: str = "MAX_SESSIONS_REACHED"
    NOT_SUPPORTED: str = "NOT_SUPPORTED"
    STALE_OBJECT: str = "STALE_OBJECT"
    DEPENDENCY_MISSING: str = "DEPENDENCY_MISSING"


def engine_error(exc: BaseException, context: str = "") -> ToolError:
    """Translate an engine or Python exception raised by a binding call.

    Engine errors keep their typed name (``LifecycleError``, ``BadParamError``,
    ...) so the caller can tell a wrong lifecycle state from a bad value.
    """
    if isinstance(exc, ToolError):
        return exc
    from openswmm.engine import EngineError, LifecycleError, StaleObjectError

    where = f" ({context})" if context else ""
    if isinstance(exc, LifecycleError):
        code = ErrorCode.INVALID_STATE
    elif isinstance(exc, StaleObjectError):
        code = ErrorCode.STALE_OBJECT
    elif isinstance(exc, KeyError):
        code = ErrorCode.ELEMENT_NOT_FOUND
    elif isinstance(exc, EngineError):
        code = ErrorCode.ENGINE_ERROR
    elif isinstance(exc, AttributeError):
        # Sub-views refuse elements of the wrong subtype (node.storage on a junction).
        code = ErrorCode.NOT_SUPPORTED
    else:
        code = ErrorCode.VALIDATION_ERROR
    message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    return ToolError(f"[{code}] {type(exc).__name__}: {message}{where}")
