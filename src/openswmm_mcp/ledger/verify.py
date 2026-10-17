"""Claim verification against a tally's committed facts."""

from __future__ import annotations

import difflib
import re
from typing import Any

from openswmm_mcp.ledger.schema import fact_id, fact_schema_entry, within_policy

__all__ = ["verify_claims"]

_BARE_RE = re.compile(r"(?<![\w.\-])-?\d+(?:,\d{3})*(?:\.\d+)?(?![\w.]\w)(?!\w)")
_CITE_RE = re.compile(r"\[f:[^\]]*\]")
_EXACT: dict[str, Any] = {"type": "exact"}
_CONTEXT = 20


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _policy_for(tally: dict, fact: dict) -> dict:
    """Policy for a fact: FACT_SCHEMA, then the tally's own schema, else exact."""
    kind, attr = fact.get("kind"), fact.get("attr")
    try:
        return fact_schema_entry(kind, attr)["policy"]
    except (KeyError, TypeError):
        pass
    try:
        attrs = (tally.get("schema") or {}).get("attributes") or {}
        entry = attrs.get(f"{kind}.{attr}")
        if entry is None and isinstance(attrs, list):
            for item in attrs:
                if isinstance(item, dict) and (item.get("kind"), item.get("attr")) == (kind, attr):
                    entry = item
                    break
        if isinstance(entry, dict) and isinstance(entry.get("policy"), dict):
            return entry["policy"]
    except (AttributeError, TypeError):
        pass
    return _EXACT


def _resolve_id(claim: dict) -> str | None:
    fid = claim.get("fact_id")
    if isinstance(fid, str) and fid:
        return fid
    kind, entity, attr = claim.get("kind"), claim.get("entity"), claim.get("attr")
    if all(isinstance(x, str) and x for x in (kind, entity, attr)):
        time = claim.get("time")
        return fact_id(kind, entity, attr, time if isinstance(time, str) and time else None)
    return None


def _suggest(fid: str, claim: dict, facts: dict[str, dict]) -> list[str]:
    found = difflib.get_close_matches(fid, list(facts), n=3, cutoff=0.6)
    entity = claim.get("entity")
    if not isinstance(entity, str):
        parts = fid.split(":")
        entity = parts[2] if len(parts) > 2 else None
    if entity and len(found) < 3:
        entities = {
            f.get("entity"): None for f in facts.values() if isinstance(f.get("entity"), str)
        }
        for ent in difflib.get_close_matches(entity, list(entities), n=3, cutoff=0.6):
            for f in facts.values():
                if f.get("entity") == ent and f["id"] not in found and len(found) < 3:
                    found.append(f["id"])
    return found[:3]


def _is_continuity(fact: dict) -> bool:
    return str(fact.get("attr", "")).endswith("continuity_error_pct")


def _verify_one(tally: dict, claim: Any, facts: dict[str, dict]) -> dict:
    claimed = claim.get("value") if isinstance(claim, dict) else None
    res: dict[str, Any] = {"claim": claim, "claimed": claimed}
    if not isinstance(claim, dict):
        return {**res, "verdict": "unsupported", "reason": "malformed"}
    fid = _resolve_id(claim)
    if fid is None or not _is_number(claimed):
        return {**res, "verdict": "unsupported", "reason": "malformed"}
    try:
        float(claimed)
    except (OverflowError, ValueError):
        return {**res, "verdict": "unsupported", "reason": "malformed"}
    res["fact_id"] = fid
    fact = facts.get(fid)
    if fact is None:
        res["verdict"] = "unsupported"
        res["suggestions"] = _suggest(fid, claim, facts)
        return res
    policy = _policy_for(tally, fact)
    expected = fact.get("value")
    res["expected"] = expected
    res["policy"] = policy
    if _is_number(expected):
        try:
            res["delta"] = float(f"{claimed - expected:.12g}")
        except ArithmeticError:
            res["delta"] = None
    ok = within_policy(expected, claimed, policy)
    unit = claim.get("unit")
    if unit is not None and unit != fact.get("unit"):
        res["unit_mismatch"] = True
        ok = False
    res["verdict"] = "matched" if ok else "altered"
    return res


def _bare_numerics(
    tally: dict, narrative: str, facts: dict[str, dict], results: list[dict]
) -> list[dict]:
    text = _CITE_RE.sub("", narrative)
    known: list[tuple[Any, dict]] = []
    for f in facts.values():
        if f.get("value") is not None:
            known.append((f["value"], _policy_for(tally, f)))
    for r in results:
        if r.get("policy") is not None and _is_number(r.get("claimed")):
            known.append((r["claimed"], r["policy"]))
    out = []
    for m in _BARE_RE.finditer(text):
        token = m.group(0)
        plain = token.replace(",", "")
        try:
            num = float(plain)
        except ValueError:
            continue
        if re.fullmatch(r"\d{4}", plain) and 1900 <= int(plain) <= 2100:
            continue
        is_int_literal = "." not in plain
        if any(
            _is_number(v)
            and (
                (is_int_literal and v == num)
                if pol.get("type") == "exact"
                else within_policy(v, num, pol)
            )
            for v, pol in known
        ):
            continue
        out.append(
            {
                "text": token,
                "context": text[max(0, m.start() - _CONTEXT) : m.end() + _CONTEXT],
            }
        )
    return out


def verify_claims(tally: dict, claims: list[dict], narrative: str | None = None) -> dict:
    """Verify numeric claims against a tally's facts.

    Parameters
    ----------
    tally : dict
        Tally as stored on disk; only ``facts`` (and ``tally_id``) are used.
    claims : list of dict
        Each ``{"fact_id", "value", "unit"?}`` or
        ``{"kind", "entity", "attr", "value", "unit"?, "time"?}``.
    narrative : str, optional
        Narrative text scanned for uncited bare numbers (advisory).

    Returns
    -------
    dict
        ``{tally_id, results, coverage, bare, summary}``. Never raises on bad input.
    """
    tally = tally if isinstance(tally, dict) else {}
    facts: dict[str, dict] = {}
    for f in tally.get("facts") or []:
        if isinstance(f, dict) and isinstance(f.get("id"), str):
            facts[f["id"]] = f
    results = [_verify_one(tally, c, facts) for c in (claims if isinstance(claims, list) else [])]

    cited_ids = {r["fact_id"] for r in results if r.get("fact_id") in facts}
    material = []
    zero_diag = 0
    for f in facts.values():
        if f.get("scope") != "diagnostic":
            continue
        v = f.get("value")
        if _is_continuity(f) or (_is_number(v) and v != 0):
            material.append(f)
        else:
            zero_diag += 1
    cited = [f for f in material if f["id"] in cited_ids]
    uncited = [f for f in material if f["id"] not in cited_ids]
    uncited.sort(
        key=lambda f: (
            not _is_continuity(f),
            -abs(f["value"]) if _is_number(f.get("value")) else 0,
        )
    )
    cov = len(cited) / len(material) if material else 1.0
    coverage = {
        "value": cov,
        "cited": len(cited),
        "total": len(material),
        "uncited": [
            {"fact_id": f["id"], "value": f.get("value"), "unit": f.get("unit")} for f in uncited
        ],
        "zero_diagnostics": zero_diag,
    }

    bare: list[dict] = []
    if isinstance(narrative, str) and narrative:
        try:
            bare = _bare_numerics(tally, narrative, facts, results)
        except Exception:  # advisory only; never raise
            bare = []

    matched = sum(r["verdict"] == "matched" for r in results)
    altered = sum(r["verdict"] == "altered" for r in results)
    unsupported = sum(r["verdict"] == "unsupported" for r in results)
    if altered or unsupported:
        status = "failed"
    elif cov == 1.0 and not bare:
        status = "verified"
    else:
        status = "verified_with_gaps"
    return {
        "tally_id": tally.get("tally_id"),
        "results": results,
        "coverage": coverage,
        "bare": bare,
        "summary": {
            "matched": matched,
            "altered": altered,
            "unsupported": unsupported,
            "coverage": cov,
            "bare_count": len(bare),
            "status": status,
        },
    }
