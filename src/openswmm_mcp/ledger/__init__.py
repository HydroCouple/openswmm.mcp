"""Tally and provenance ledger: verifiable facts about SWMM runs.

This package's ``__init__`` imports only standard-library modules so that
``python -m openswmm_mcp.ledger`` (the offline proof checker) runs without the
engine. ``facts`` reads the engine and is imported directly, never from here.
"""

from __future__ import annotations

from openswmm_mcp.ledger.merkle import (
    build_tree,
    canonical_line,
    commit_facts,
    disclose,
    inclusion_path,
    leaf_hash,
    leaf_key,
    node_hash,
    published_root,
    tree_root,
    verify_disclosure,
)
from openswmm_mcp.ledger.schema import (
    FACT_SCHEMA,
    SCHEMA_VERSION,
    bin_value,
    fact_id,
    fact_schema_entry,
    within_policy,
)
from openswmm_mcp.ledger.store import LedgerStore
from openswmm_mcp.ledger.verify import verify_claims

__all__ = [
    "FACT_SCHEMA",
    "SCHEMA_VERSION",
    "LedgerStore",
    "bin_value",
    "build_tree",
    "canonical_line",
    "commit_facts",
    "disclose",
    "fact_id",
    "fact_schema_entry",
    "inclusion_path",
    "leaf_hash",
    "leaf_key",
    "node_hash",
    "published_root",
    "tree_root",
    "verify_claims",
    "verify_disclosure",
    "within_policy",
]
