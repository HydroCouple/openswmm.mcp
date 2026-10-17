"""Shared constants, canonical times and the CRS compatibility rule for the provenance ledger.

Standard library only. The CRS rule never mixes frames silently: a model without a CRS works
in ``local:<map units>``; a declared CRS is explicit; a source whose frame cannot be aligned
with the model's is classified here (``crs_verdict``) so ``locate`` can leave it out and list
it, and ``link(rule="nearest")`` can refuse. Reprojection between two declared CRSs is decided
later, in ``spatial.py``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

__all__ = [
    "ELEMENT_KINDS",
    "FORMATS",
    "MAP_UNITS",
    "ROLES",
    "SOURCE_ID",
    "CrsRefusal",
    "canonical_time",
    "check_model_crs",
    "crs_class",
    "crs_label",
    "crs_verdict",
    "next_after",
    "resolve_model_crs",
    "utc_now",
]

ROLES = (
    "network",
    "nodes",
    "survey",
    "asbuilt",
    "rain",
    "flow",
    "scada",
    "document",
    "model",
    "other",
)
# Extension -> format. ``.json`` is promoted to ``geojson`` by ingest when it holds a
# FeatureCollection or Feature.
FORMATS = {
    ".geojson": "geojson",
    ".gpkg": "gpkg",
    ".shp": "shp",
    ".csv": "csv",
    ".inp": "inp",
    ".pdf": "pdf",
    ".md": "md",
    ".txt": "txt",
    ".json": "json",
}
ELEMENT_KINDS = ("node", "link", "subcatchment", "gage")
# [MAP] Units value -> the unit name stated next to every buffer and distance.
MAP_UNITS = {"feet": "ft", "meters": "m", "degrees": "deg", "none": "map unit"}
SOURCE_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_EPSG = re.compile(r"(?:EPSG:|urn:ogc:def:crs:EPSG:[^:]*:)(\d+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
def canonical_time(value: Any) -> str | None:
    """Fixed-width UTC text ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (text order is time order).

    Accepts None, a ``datetime``/``date``, or ISO date/datetime text; naive values are UTC.

    Raises
    ------
    ValueError
        If the value is not a recognisable date or datetime.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"not an ISO date or datetime: {value!r}") from exc
    else:
        raise ValueError(f"not a date or datetime: {value!r}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def utc_now() -> str:
    """The current time as canonical text."""
    return canonical_time(datetime.now(timezone.utc))


def next_after(stamp: str) -> str:
    """The canonical time one microsecond after ``stamp`` (keeps ``recorded_at`` increasing)."""
    moment = datetime.strptime(stamp, _TIME_FORMAT).replace(tzinfo=timezone.utc)
    return canonical_time(moment + timedelta(microseconds=1))


# ---------------------------------------------------------------------------
# CRS labels
# ---------------------------------------------------------------------------
class CrsRefusal(Exception):
    """A CRS problem: ``code`` is an ``ErrorCode`` name, ``slug`` a stable reason id."""

    def __init__(self, code: str, slug: str, message: str) -> None:
        super().__init__(message)
        self.code, self.slug, self.message = code, slug, message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "slug": self.slug, "reason": self.message}


def crs_label(text: str | None, map_units: str | None = None) -> str:
    """Normalise a CRS description to a ledger label.

    ``EPSG:<n>`` for EPSG codes and OGC URNs; ``local:<feet|meters|degrees|none>``;
    ``unknown``; ``none`` (non-spatial); any other text (WKT, PROJ) is kept, stripped.
    Empty text gives ``local:<map_units>`` when ``map_units`` is given, else ``unknown``.

    Raises
    ------
    ValueError
        For ``local:`` with a unit outside ``MAP_UNITS``.
    """
    raw = (text or "").strip()
    if not raw:
        return f"local:{map_units.lower()}" if map_units else "unknown"
    lowered = raw.lower()
    if lowered in ("unknown", "none"):
        return lowered
    if lowered.startswith("local:"):
        unit = lowered.split(":", 1)[1].strip()
        if unit not in MAP_UNITS:
            raise ValueError(f"local CRS unit must be one of {sorted(MAP_UNITS)}; got {unit!r}")
        return f"local:{unit}"
    m = _EPSG.fullmatch(raw)
    if m:
        return f"EPSG:{int(m.group(1))}"
    return raw


def crs_class(label: str) -> str:
    """``"local"``, ``"unknown"``, ``"none"`` or ``"declared"``."""
    if label.startswith("local:"):
        return "local"
    if label in ("unknown", "none"):
        return label
    return "declared"


def resolve_model_crs(
    argument: str | None,
    registered: str | None,
    engine: str | None,
    map_units: str | None,
) -> tuple[str, str, list[str]]:
    """The model's CRS label for one call, where it came from, and warnings.

    First hit wins: the call's ``crs`` argument, the CRS registered for the model's sha256,
    the engine's ``spatial.crs``, then ``local:<map units>``.

    Raises
    ------
    CrsRefusal
        ``crs_conflict`` when the argument and the registered CRS differ.
    """
    warnings: list[str] = []
    arg = crs_label(argument) if argument else None
    reg = crs_label(registered) if registered else None
    eng = crs_label(engine) if engine else None
    if arg and reg and arg != reg:
        raise CrsRefusal(
            "VALIDATION_ERROR",
            "crs_conflict",
            f"crs={arg} differs from the CRS registered for this model ({reg}). "
            "Re-register the model with the right crs, or omit crs.",
        )
    if reg and eng and reg != eng:
        warnings.append(f"Registered model CRS {reg} differs from the engine's {eng}; using {reg}.")
    if arg:
        return arg, "argument", warnings
    if reg:
        return reg, "registered", warnings
    if eng:
        return eng, "engine", warnings
    return crs_label(None, map_units or "none"), "map_units", warnings


def check_model_crs(label: str) -> None:
    """Refuse model frames in which a buffer is not a distance.

    Raises
    ------
    CrsRefusal
        ``crs_required`` for ``local:degrees`` (degrees without a datum are not a distance).
    """
    if label == "local:degrees":
        raise CrsRefusal(
            "VALIDATION_ERROR",
            "crs_required",
            "The model's map units are degrees but no CRS is declared, so a buffer would be in "
            "degrees, which is not a distance. Declare the CRS with locate(crs=...) or "
            "sources(action='register', role='model', crs=...).",
        )


def crs_verdict(model_label: str, source_label: str) -> CrsRefusal | None:
    """Classify one spatial source against the model frame.

    Returns None when the source can be used: the same local frame, a non-spatial source,
    or two declared CRSs (``spatial.py`` reprojects). Otherwise the ``CrsRefusal`` that
    ``locate`` lists under ``excluded.crs_incompatible`` and ``link(rule="nearest")`` raises.
    """
    m_class, s_class = crs_class(model_label), crs_class(source_label)
    if s_class == "none":
        return None
    if s_class == "unknown":
        return CrsRefusal(
            "VALIDATION_ERROR",
            "crs_unknown",
            f"Its CRS is unknown. Re-register it with sources(action='register', crs=...) to "
            f"use it against the model frame {model_label}.",
        )
    if m_class == "local" and s_class == "local":
        if model_label == source_label:
            return None
        detail = "the two local frames may not share an origin or unit"
    elif m_class == "declared" and s_class == "declared":
        return None
    elif m_class == "local":
        detail = f"the model has no CRS (frame {model_label}) but the source is {source_label}"
    else:
        detail = f"the model is {model_label} but the source is in local frame {source_label}"
    return CrsRefusal(
        "NOT_SUPPORTED",
        "crs_mismatch",
        f"{detail}. Declare the model CRS with locate(crs=...) or "
        "sources(action='register', role='model', crs=...) to include it. Nothing was "
        "reprojected.",
    )
