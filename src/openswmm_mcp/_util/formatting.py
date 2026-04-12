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
