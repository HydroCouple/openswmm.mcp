"""Unit tests for ledger verification and schema binning (no engine)."""

from __future__ import annotations

from openswmm_mcp.ledger.schema import bin_value, fact_id, within_policy
from openswmm_mcp.ledger.verify import verify_claims

SIG3 = {"type": "sigfig", "n": 3}
ABS = {"type": "abs", "tol": 0.01}
EXACT = {"type": "exact"}


def _f(kind, entity, attr, value, unit="", scope="output"):
    return {
        "id": fact_id(kind, entity, attr),
        "scope": scope,
        "entity": entity,
        "kind": kind,
        "attr": attr,
        "value": value,
        "unit": unit,
        "time": None,
    }


def _tally(*facts):
    return {"tally_id": "t1", "facts": list(facts)}


C2 = _f("link", "C2", "max_flow", 14.2, "CFS")
J1 = _f("node", "J1", "vol_flooded", 4.3, "10^6 gal", "diagnostic")
CONT = _f("system", "system", "routing_continuity_error_pct", -0.11, "%", "diagnostic")
ZERO = _f("system", "system", "n_steps_not_converged", 0, "", "diagnostic")
ZCONT = _f("system", "system", "runoff_continuity_error_pct", 0.0, "%", "diagnostic")


def test_bin_value_cases():
    assert bin_value(14.2069, SIG3) == 14.2
    assert bin_value(-0.111104, ABS) == -0.11
    assert bin_value(-0.0, SIG3) == 0.0
    assert bin_value(0.03000001, ABS) == 0.03
    assert bin_value(float("nan"), SIG3) is None
    assert bin_value(None, EXACT) is None
    assert bin_value(3.6, EXACT) == 4


def test_within_policy_cases():
    assert within_policy(14.2, 14.24, SIG3)
    assert not within_policy(14.2, 14.3, SIG3)
    assert within_policy(0.03, 0.04, ABS)
    assert not within_policy(0.03, 0.05, ABS)
    assert within_policy(3, 3, EXACT)
    assert not within_policy(3, 4, EXACT)
    assert not within_policy(None, 1, SIG3)


def test_matched_altered_unsupported():
    t = _tally(C2)
    r = verify_claims(
        t,
        [
            {"fact_id": C2["id"], "value": 14.2},
            {"kind": "link", "entity": "C2", "attr": "max_flow", "value": 20.0},
            {"fact_id": "f:link:ZZZ:max_flow", "value": 1.0},
        ],
    )
    assert [x["verdict"] for x in r["results"]] == ["matched", "altered", "unsupported"]
    assert r["results"][1]["expected"] == 14.2
    assert r["results"][1]["delta"] == 5.8
    assert r["summary"]["status"] == "failed"
    assert r["tally_id"] == "t1"


def test_typo_suggests_close_id():
    r = verify_claims(_tally(C2), [{"fact_id": "f:link:C22:max_flow", "value": 14.2}])
    assert r["results"][0]["verdict"] == "unsupported"
    assert C2["id"] in r["results"][0]["suggestions"]


def test_unit_mismatch_altered():
    r = verify_claims(_tally(C2), [{"fact_id": C2["id"], "value": 14.2, "unit": "CMS"}])
    assert r["results"][0]["verdict"] == "altered"
    assert r["results"][0]["unit_mismatch"] is True
    ok = verify_claims(_tally(C2), [{"fact_id": C2["id"], "value": 14.2, "unit": "CFS"}])
    assert ok["results"][0]["verdict"] == "matched"


def test_malformed_never_raises():
    r = verify_claims(
        _tally(C2),
        [
            {"value": 1},
            {"fact_id": C2["id"], "value": "14.2"},
            {"fact_id": C2["id"], "value": True},
            5,
        ],
        narrative=None,
    )
    assert all(x["verdict"] == "unsupported" and x["reason"] == "malformed" for x in r["results"])


def test_coverage_zero_handling():
    t = _tally(C2, J1, CONT, ZERO, ZCONT)
    r = verify_claims(t, [{"fact_id": J1["id"], "value": 4.3}])
    cov = r["coverage"]
    # material: J1, CONT, ZCONT (zero continuity counts); ZERO is count-only
    assert cov["total"] == 3
    assert cov["cited"] == 1
    assert cov["zero_diagnostics"] == 1
    assert cov["uncited"][0]["fact_id"] in {CONT["id"], ZCONT["id"]}
    assert cov["uncited"][-1]["fact_id"] != J1["id"]
    assert r["summary"]["status"] == "verified_with_gaps"


def test_coverage_empty_is_one():
    r = verify_claims(_tally(C2), [])
    assert r["coverage"]["value"] == 1.0
    assert r["summary"]["status"] == "verified"


def test_bare_numerics():
    t = _tally(C2, J1)
    claims = [{"fact_id": C2["id"], "value": 14.2}, {"fact_id": J1["id"], "value": 4.3}]
    text = (
        "In 2024 link C2 peaked at 14.2 [f:link:C2:max_flow] "
        "and J1 flooded 4.3 [f:node:J1:vol_flooded]."
    )
    assert verify_claims(t, claims, text)["bare"] == []
    r = verify_claims(
        t, [claims[0]], "In 2024 link C2 peaked at 14.2 and J1 had 7.9 cfs, 1,250 more."
    )
    texts = [b["text"] for b in r["bare"]]
    assert texts == ["7.9", "1,250"]
    assert "7.9" in r["bare"][0]["context"]
    assert r["summary"]["bare_count"] == 2


def test_status_outcomes():
    t = _tally(C2, J1)
    full = [{"fact_id": J1["id"], "value": 4.3}]
    assert verify_claims(t, full)["summary"]["status"] == "verified"
    assert verify_claims(t, full, "uncited 9.9")["summary"]["status"] == "verified_with_gaps"
    assert verify_claims(t, [])["summary"]["status"] == "verified_with_gaps"
    assert verify_claims(t, [{"fact_id": J1["id"], "value": 5.9}])["summary"]["status"] == "failed"


def test_bare_decimal_not_excused_by_exact_fact():
    nf = _f("system", "system", "nodes_flooded", 4, "", "diagnostic")
    r = verify_claims(_tally(nf), [], "Found 4.3 and 4 here.")
    texts = [b["text"] for b in r["bare"]]
    assert "4.3" in texts
    assert "4" not in texts


def test_huge_values_never_raise():
    huge = 10**400
    nf = _f("system", "system", "nodes_flooded", 4, "", "diagnostic")
    facts = _tally(C2, CONT, nf)
    r = verify_claims(
        facts,
        [
            {"fact_id": C2["id"], "value": huge},
            {"fact_id": CONT["id"], "value": huge},
            {"fact_id": nf["id"], "value": huge},
        ],
        "x " + "1" * 400 + " y",
    )
    assert [(x["verdict"], x.get("reason")) for x in r["results"]] == [
        ("unsupported", "malformed")
    ] * 3
    assert r["summary"]["status"] == "failed"
    assert bin_value(huge, SIG3) is None
    assert within_policy(1.0, huge, SIG3) is False
