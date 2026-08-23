"""Formatting helpers for converting engine data into human-/LLM-friendly strings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def ndarray_to_list(arr: Any) -> list:
    """Convert a NumPy ndarray (or ``None``) to a plain Python list.

    Parameters
    ----------
    arr:
        A NumPy array, a sequence, or ``None``.

    Returns
    -------
    list
        A plain Python list.  Returns an empty list when *arr* is ``None``.
    """
    if arr is None:
        return []
    # Use .tolist() when available (numpy ndarray, pandas Series, etc.)
    if hasattr(arr, "tolist"):
        return arr.tolist()
    return list(arr)


def format_elapsed(days: float) -> str:
    """Convert a decimal number of days to an ``"HH:MM:SS"`` string.

    Parameters
    ----------
    days:
        Elapsed time expressed as fractional days (e.g. 0.5 == 12 hours).

    Returns
    -------
    str
        A string in ``"HH:MM:SS"`` format.

    Examples
    --------
    >>> format_elapsed(0.5)
    '12:00:00'
    >>> format_elapsed(1.25)
    '30:00:00'
    """
    total_seconds = int(round(days * 86400))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


# The Julian Day Number epoch used by SWMM (matches the astronomical JDN
# convention where JDN 0 == 1 January 4713 BC in the proleptic Julian
# calendar).  Python's ``datetime`` works with the proleptic Gregorian
# calendar, so we use the well-known offset:
#   datetime(2000, 1, 1, 12, 0, 0) corresponds to JDN 2_451_545.0
_JDN_EPOCH = 2_451_545.0  # J2000.0
_J2000_DT = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def format_julian(julian: float) -> str:
    """Convert a Julian Day Number to an ISO-style datetime string.

    Parameters
    ----------
    julian:
        A Julian Day Number (e.g. 2_460_000.5).

    Returns
    -------
    str
        Datetime formatted as ``"YYYY-MM-DD HH:MM:SS"``.

    Examples
    --------
    >>> format_julian(2451545.0)
    '2000-01-01 12:00:00'
    """
    delta_days = julian - _JDN_EPOCH
    dt = _J2000_DT + timedelta(days=delta_days)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def truncate_list(items: list, max_items: int = 1000) -> tuple[list, bool]:
    """Return at most *max_items* elements from *items*.

    Parameters
    ----------
    items:
        The source list.
    max_items:
        Maximum number of elements to keep.  Defaults to 1000.

    Returns
    -------
    tuple[list, bool]
        A two-element tuple ``(truncated_list, was_truncated)`` where
        *was_truncated* is ``True`` when elements were dropped.

    Examples
    --------
    >>> truncate_list([1, 2, 3], max_items=2)
    ([1, 2], True)
    >>> truncate_list([1, 2, 3], max_items=5)
    ([1, 2, 3], False)
    """
    if len(items) <= max_items:
        return items, False
    return items[:max_items], True


def paginate_list(
    items: list,
    start_index: int = 0,
    limit: int | None = None,
) -> tuple[list, dict[str, int | bool]]:
    """Slice *items* using ``start_index``/``limit`` for tool responses.

    This is the canonical pagination primitive for the MCP server's
    O(n_elements) list-returning tools (``get_node_info``,
    ``get_link_info``, ``find_elements``, ``output_*_results`` ...).
    Tools call ``paginate_list`` after the bulk fetch — slicing is cheap
    relative to the fetch and ensures every caller observes consistent
    pagination semantics.

    Parameters
    ----------
    items:
        The source list (already materialised by the tool — pagination
        does not lazy-fetch).
    start_index:
        Zero-based offset of the first item to return.  Negative values
        are clamped to ``0``; values past the end produce an empty slice.
    limit:
        Maximum number of items in the returned slice, or ``None`` for
        "no limit" (the whole tail from ``start_index``).  Non-positive
        values produce an empty slice (cleaner than raising — callers
        often build URLs from user input where 0 means "do not return").

    Returns
    -------
    tuple[list, dict]
        ``(slice, meta)`` where ``meta`` carries:

          * ``total``         — original item count
          * ``start_index``   — clamped offset actually used
          * ``limit``         — limit applied (``-1`` for "no limit")
          * ``returned``      — ``len(slice)``
          * ``has_more``      — ``True`` if items remain after the slice

    Examples
    --------
    >>> paginate_list([1, 2, 3, 4, 5], start_index=1, limit=2)
    ([2, 3], {'total': 5, 'start_index': 1, 'limit': 2, 'returned': 2, 'has_more': True})
    >>> paginate_list([1, 2, 3], start_index=0, limit=None)[0]
    [1, 2, 3]
    >>> paginate_list([], start_index=0, limit=10)[0]
    []
    """
    total = len(items)
    start = max(0, int(start_index))
    if limit is None:
        sl = items[start:]
        eff_limit = -1
    elif limit <= 0:
        sl = []
        eff_limit = int(limit)
    else:
        sl = items[start : start + int(limit)]
        eff_limit = int(limit)
    meta = {
        "total": total,
        "start_index": start,
        "limit": eff_limit,
        "returned": len(sl),
        "has_more": (start + len(sl)) < total,
    }
    return sl, meta
