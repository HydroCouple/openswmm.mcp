"""Salted, domain-separated Merkle commitment over facts (RFC 6962 style).

Standard library only; this module must not import ``openswmm`` or any other
``openswmm_mcp`` module, because the CLI and reviewers use it standalone.

Construction (all hashing over raw bytes, hex only in JSON)::

    line_i = "|".join([scope, kind, entity, attr, repr_value, unit, time or ""])
    k_i    = HMAC-SHA256(S, b"openswmm-ledger/v1/leaf-key|" + fact_id)
    leaf_i = SHA256(b"\\x00" + HMAC-SHA256(k_i, line_i))
    node   = SHA256(b"\\x01" + left + right)

An odd node at the end of a level is promoted unchanged. The tree is built over
the leaves only; the *published* root additionally commits to the fact count::

    root = SHA256(b"\x02" + fact_count.to_bytes(8, "big") + tree_root)

The fact id is bound by checking ``id == f:{kind}:{entity}:{attr}[:{time}]``.
"""

from __future__ import annotations

import hashlib
import hmac
from numbers import Integral
from typing import Any

__all__ = [
    "canonical_line",
    "leaf_key",
    "leaf_hash",
    "node_hash",
    "build_tree",
    "tree_root",
    "published_root",
    "fact_id_of",
    "inclusion_path",
    "commit_facts",
    "disclose",
    "verify_disclosure",
]

_KEY_TAG = b"openswmm-ledger/v1/leaf-key|"
_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"
_ROOT_PREFIX = b"\x02"
_FIELDS = ("scope", "kind", "entity", "attr", "unit")


def canonical_line(fact: dict[str, Any]) -> str:
    """Return the canonical pipe-joined line for a fact.

    Parameters
    ----------
    fact : dict
        Fact dict with ``scope, kind, entity, attr, value, unit, time``.

    Returns
    -------
    str
        ``scope|kind|entity|attr|repr_value|unit|time``.

    Raises
    ------
    TypeError
        If ``value`` is not an ``int`` or ``float`` (``bool`` is refused).
    """
    value = fact["value"]
    if isinstance(value, bool) or not isinstance(value, (Integral, float)):
        raise TypeError(f"fact value must be int or float, got {type(value).__name__}")
    # float()/int() make numpy scalars produce the same text as stdlib values.
    text = repr(int(value)) if isinstance(value, Integral) else repr(float(value))
    time = fact.get("time")
    time = time if time else ""
    for name, part in [(n, fact[n]) for n in _FIELDS] + [("time", time)]:
        if not isinstance(part, str):
            raise TypeError(f"fact {name} must be str")
        if "|" in part:
            raise ValueError(f"fact {name} must not contain '|'")
    return "|".join(
        [fact["scope"], fact["kind"], fact["entity"], fact["attr"], text, fact["unit"], time]
    )


def fact_id_of(fact: dict[str, Any]) -> str:
    """Return the id implied by a fact's kind, entity, attr and time."""
    base = f"f:{fact['kind']}:{fact['entity']}:{fact['attr']}"
    return f"{base}:{fact['time']}" if fact.get("time") else base


def leaf_key(salt: bytes, fact_id: str) -> bytes:
    """Return the per-fact key ``k_i`` derived from the master salt."""
    return hmac.new(salt, _KEY_TAG + fact_id.encode(), hashlib.sha256).digest()


def leaf_hash(key: bytes, line: str) -> bytes:
    """Return the leaf digest for a canonical line under per-fact key ``key``."""
    inner = hmac.new(key, line.encode(), hashlib.sha256).digest()
    return hashlib.sha256(_LEAF_PREFIX + inner).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """Return the internal-node digest of two child digests."""
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


def build_tree(leaves: list[bytes]) -> list[list[bytes]]:
    """Build the tree; levels are leaves first, root level (one digest) last.

    Raises
    ------
    ValueError
        If ``leaves`` is empty.
    """
    if not leaves:
        raise ValueError("cannot build a Merkle tree with zero leaves")
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt = [node_hash(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
        if len(cur) % 2:
            nxt.append(cur[-1])
        levels.append(nxt)
    return levels


def tree_root(levels: list[list[bytes]]) -> bytes:
    """Return the root digest of built ``levels``."""
    return levels[-1][0]


def published_root(root: bytes, fact_count: int) -> bytes:
    """Return the published root, which commits to the fact count.

    Parameters
    ----------
    root : bytes
        The tree root from :func:`tree_root`.
    fact_count : int
        Number of facts (int >= 1; bool refused).
    """
    if isinstance(fact_count, bool) or not isinstance(fact_count, int) or fact_count < 1:
        raise ValueError("fact_count must be an int >= 1")
    return hashlib.sha256(_ROOT_PREFIX + fact_count.to_bytes(8, "big") + root).digest()


def _expected_sides(index: int, count: int) -> list[str]:
    """Sibling sides for leaf ``index`` in a tree of ``count`` leaves."""
    sides: list[str] = []
    n = count
    while n > 1:
        if index % 2:
            sides.append("L")
        elif index + 1 < n:
            sides.append("R")
        index //= 2
        n = (n + 1) // 2
    return sides


def inclusion_path(levels: list[list[bytes]], index: int) -> list[dict[str, str]]:
    """Return the audit path for leaf ``index``.

    Each step is ``{"side": "L"|"R", "hash": hex}`` where ``side`` is the
    sibling's side. A promoted node contributes no step.
    """
    if not 0 <= index < len(levels[0]):
        raise IndexError(f"leaf index {index} out of range")
    path: list[dict[str, str]] = []
    for level in levels[:-1]:
        if index % 2:
            path.append({"side": "L", "hash": level[index - 1].hex()})
        elif index + 1 < len(level):
            path.append({"side": "R", "hash": level[index + 1].hex()})
        index //= 2
    return path


def commit_facts(
    salt: bytes, facts_sorted: list[dict[str, Any]]
) -> tuple[list[list[bytes]], list[bytes]]:
    """Build the tree for facts already sorted by id.

    Returns
    -------
    tuple
        ``(levels, leaves)``.
    """
    leaves = [leaf_hash(leaf_key(salt, f["id"]), canonical_line(f)) for f in facts_sorted]
    return build_tree(leaves), leaves


def disclose(
    salt: bytes,
    facts_sorted: list[dict[str, Any]],
    index: int,
    levels: list[list[bytes]],
) -> dict[str, Any]:
    """Build the disclosure for fact ``index`` (see module docstring).

    Returns
    -------
    dict
        ``{fact, line, key, index, fact_count, path}``; ``fact`` has no
        ``leaf`` key, ``key`` is the hex of ``k_i``.
    """
    fact = {k: v for k, v in facts_sorted[index].items() if k != "leaf"}
    return {
        "fact": fact,
        "line": canonical_line(fact),
        "key": leaf_key(salt, fact["id"]).hex(),
        "index": index,
        "fact_count": len(facts_sorted),
        "path": inclusion_path(levels, index),
    }


def verify_disclosure(disclosure: dict[str, Any], root_hex: str, fact_count: int) -> bool:
    """Check a disclosure against a public root and fact count.

    Pure: recomputes the leaf from ``line`` and ``key``, folds the path to the
    tree root, and compares :func:`published_root` of it with ``root_hex``. Also
    checks that the fact id matches its kind/entity/attr/time, and that the line matches the fact,
    that ``fact_count`` matches, and that the path shape fits ``index``.
    Never raises; any malformed input returns ``False``.
    """
    try:
        if isinstance(fact_count, bool) or not isinstance(fact_count, int) or fact_count < 1:
            return False
        if disclosure["fact_count"] != fact_count:
            return False
        index = disclosure["index"]
        if isinstance(index, bool) or not isinstance(index, int):
            return False
        if not 0 <= index < fact_count:
            return False
        line = disclosure["line"]
        if not isinstance(line, str) or canonical_line(disclosure["fact"]) != line:
            return False
        if disclosure["fact"]["id"] != fact_id_of(disclosure["fact"]):
            return False
        path = disclosure["path"]
        if [s["side"] for s in path] != _expected_sides(index, fact_count):
            return False
        digest = leaf_hash(bytes.fromhex(disclosure["key"]), line)
        for step in path:
            sibling = bytes.fromhex(step["hash"])
            if len(sibling) != 32:
                return False
            digest = (
                node_hash(sibling, digest) if step["side"] == "L" else node_hash(digest, sibling)
            )
        return hmac.compare_digest(published_root(digest, fact_count), bytes.fromhex(root_hex))
    except Exception:
        return False
