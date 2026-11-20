"""Input validation utilities for MCP tool handlers."""

from __future__ import annotations

from pathlib import Path

from fastmcp.exceptions import ToolError

# Canonical element types supported by the OpenSWMM engine queries.
VALID_ELEMENT_TYPES: frozenset[str] = frozenset({"node", "link", "subcatchment", "gage", "system"})


def resolve_path(path: str, working_dir: str) -> Path:
    """Resolve *path* against *working_dir*, expanding ``~``.

    Parameters
    ----------
    path:
        A file-system path that may be relative or contain ``~``.
    working_dir:
        The directory used as the base when *path* is relative.

    Returns
    -------
    Path
        A fully resolved, absolute :class:`~pathlib.Path`.

    Examples
    --------
    >>> resolve_path("model.inp", "/data/projects")
    PosixPath('/data/projects/model.inp')
    >>> resolve_path("~/models/test.inp", "/ignored")  # doctest: +SKIP
    PosixPath('/home/user/models/test.inp')
    """
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (Path(working_dir).expanduser() / expanded).resolve()


def validate_element_type(element_type: str) -> str:
    """Validate that *element_type* is a recognised SWMM element category.

    The comparison is case-insensitive; the returned value is always lowercase.

    Parameters
    ----------
    element_type:
        A string such as ``"node"``, ``"Link"``, or ``"SUBCATCHMENT"``.

    Returns
    -------
    str
        The normalised (lowercase) element type.

    Raises
    ------
    ToolError
        If *element_type* is not one of the accepted values.
    """
    normalised = element_type.strip().lower()
    if normalised not in VALID_ELEMENT_TYPES:
        sorted_types = ", ".join(f"'{t}'" for t in sorted(VALID_ELEMENT_TYPES))
        raise ToolError(f"Unknown element type '{element_type}'. Valid types are: {sorted_types}.")
    return normalised


def validate_session_state(
    session,
    *valid_states: str,
    action: str = "perform this action",
) -> None:
    """Raise :class:`ToolError` if the session is not in an accepted state.

    Parameters
    ----------
    session:
        A session object exposing a ``.state`` attribute.
    *valid_states:
        One or more acceptable state strings (e.g. ``"running"``, ``"paused"``).
    action:
        A human-readable description of what the caller is trying to do,
        used in the error message.  Defaults to ``"perform this action"``.

    Raises
    ------
    ToolError
        If ``session.state`` is not among *valid_states*.
    """
    if session.state not in valid_states:
        allowed = ", ".join(f"'{s}'" for s in valid_states)
        raise ToolError(
            f"Cannot {action}: session is in state '{session.state}', "
            f"but must be in one of: {allowed}."
        )
