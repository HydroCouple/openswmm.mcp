"""Canonical cross-section shape name -> engine code map.

Derived from the engine's own ``XSectShape`` IntEnum, which is pinned
against ``openswmm_links.h`` by the engine's ``test_enum_parity``. Deriving
rather than transcribing is deliberate: the codes were **renumbered in
6.0**, and the hand-written maps that previously lived in
``tools/editing.py`` and ``tools/building.py`` still carried the legacy
SWMM 5 ``XsectType`` ordering. Every shape except ``circular`` therefore
resolved to a different geometry than the caller asked for -- e.g.
``trapezoidal`` (3) was stored as ``RECT_OPEN``, and ``irregular`` (17) as
``RECT_ROUND``.

``LEGACY_ALIASES`` keeps the two spellings those maps used which are not
enum member names, so callers written against the old tools keep working
-- now resolving to the correct code.
"""

from __future__ import annotations

from openswmm_mcp.errors import ErrorCode, ToolError

# Spellings accepted by the pre-6.0 maps that are not enum member names.
LEGACY_ALIASES: dict[str, str] = {
    "powerfunc": "power",
    "egg": "eggshaped",
    "street": "street_xsect",
}


def _build() -> dict[str, int]:
    from openswmm.engine import XSectShape

    shapes = {s.name.lower(): int(s) for s in XSectShape}
    for alias, target in LEGACY_ALIASES.items():
        shapes[alias] = shapes[target]
    return shapes


_SHAPES: dict[str, int] | None = None


def shape_codes() -> dict[str, int]:
    """Return the shape-name -> engine-code map, built on first use.

    Built lazily so importing a tool module does not require the compiled
    ``openswmm`` extension (the legacy backend does not provide it).
    """
    global _SHAPES
    if _SHAPES is None:
        _SHAPES = _build()
    return _SHAPES


def resolve_shape(shape: str) -> int:
    """Resolve a shape name to its engine code.

    @param shape: Shape name, case-insensitive (e.g. ``"trapezoidal"``).
    @raise ToolError: If the name is not a known shape.
    """
    codes = shape_codes()
    key = shape.strip().lower()
    if key not in codes:
        valid = ", ".join(sorted(k for k in codes if k not in LEGACY_ALIASES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown cross-section shape '{shape}'. "
            f"Valid shapes: {valid}."
        )
    return codes[key]
