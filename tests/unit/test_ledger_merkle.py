"""Merkle commitment tests (pure logic, no engine)."""

from __future__ import annotations

import copy
import hashlib
import hmac

import pytest

from openswmm_mcp.ledger import merkle

SALT = bytes(range(32))


def _fact(i: int, value=1.5) -> dict:
    return {
        "id": f"f:link:C{i}:max_flow",
        "scope": "output",
        "entity": f"C{i}",
        "kind": "link",
        "attr": "max_flow",
        "value": value,
        "unit": "CFS",
        "time": None,
    }


def _facts(n: int) -> list[dict]:
    return sorted((_fact(i, float(i) + 0.25) for i in range(n)), key=lambda f: f["id"])


def _disclosures(n: int, salt: bytes = SALT):
    facts = _facts(n)
    levels, _ = merkle.commit_facts(salt, facts)
    root = merkle.published_root(merkle.tree_root(levels), n).hex()
    return facts, levels, root, [merkle.disclose(salt, facts, i, levels) for i in range(n)]


def test_known_answer_root():
    facts = [
        {"id": "f:link:C1:max_flow", "scope": "output", "kind": "link", "entity": "C1",
         "attr": "max_flow", "value": 14.2, "unit": "CFS", "time": None},
        {"id": "f:node:J1:max_depth", "scope": "output", "kind": "node", "entity": "J1",
         "attr": "max_depth", "value": 3, "unit": "ft", "time": None},
        {"id": "f:system:system:nodes_flooded", "scope": "diagnostic", "kind": "system",
         "entity": "system", "attr": "nodes_flooded", "value": 0, "unit": "count", "time": None},
    ]  # fmt: skip

    def sha(b):
        return hashlib.sha256(b).digest()

    leaves = []
    for f in facts:
        line = "|".join(
            [f["scope"], f["kind"], f["entity"], f["attr"], repr(f["value"]), f["unit"], ""]
        )
        k = hmac.new(SALT, b"openswmm-ledger/v1/leaf-key|" + f["id"].encode(), "sha256").digest()
        leaves.append(sha(b"\x00" + hmac.new(k, line.encode(), "sha256").digest()))
    n01 = sha(b"\x01" + leaves[0] + leaves[1])
    tree = sha(b"\x01" + n01 + leaves[2])  # leaf 2 promoted, then paired with n01
    expected = sha(b"\x02" + (3).to_bytes(8, "big") + tree)

    levels, got_leaves = merkle.commit_facts(SALT, facts)
    assert got_leaves == leaves
    assert merkle.tree_root(levels) == tree
    assert merkle.published_root(tree, 3) == expected


def test_canonical_line_types():
    assert merkle.canonical_line(_fact(1, 3)) == "output|link|C1|max_flow|3|CFS|"
    assert merkle.canonical_line(_fact(1, 0.1)) == "output|link|C1|max_flow|0.1|CFS|"
    with pytest.raises(TypeError):
        merkle.canonical_line(_fact(1, True))
    with pytest.raises(TypeError):
        merkle.canonical_line(_fact(1, "1"))


def test_empty_tree_refused():
    with pytest.raises(ValueError):
        merkle.build_tree([])


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8])
def test_round_trip_every_leaf(n):
    _, _, root, discs = _disclosures(n)
    for d in discs:
        assert "leaf" not in d["fact"]
        assert merkle.verify_disclosure(d, root, n)


def test_tampering_fails():
    n = 5
    _, _, root, discs = _disclosures(n)
    d = discs[2]
    assert d["path"]

    def flip_hex(h: str) -> str:
        return f"{int(h[0], 16) ^ 1:x}" + h[1:]

    bad = copy.deepcopy(d)
    bad["line"] = bad["line"].replace("1.", "9.", 1) if "1." in bad["line"] else bad["line"] + "x"
    assert not merkle.verify_disclosure(bad, root, n)

    bad = copy.deepcopy(d)
    bad["fact"]["value"] = 99.0
    assert not merkle.verify_disclosure(bad, root, n)

    bad = copy.deepcopy(d)
    bad["key"] = flip_hex(bad["key"])
    assert not merkle.verify_disclosure(bad, root, n)

    for i in range(len(d["path"])):
        bad = copy.deepcopy(d)
        bad["path"][i]["hash"] = flip_hex(bad["path"][i]["hash"])
        assert not merkle.verify_disclosure(bad, root, n)
        bad = copy.deepcopy(d)
        bad["path"][i]["side"] = "L" if bad["path"][i]["side"] == "R" else "R"
        assert not merkle.verify_disclosure(bad, root, n)

    assert not merkle.verify_disclosure(d, flip_hex(root), n)
    assert not merkle.verify_disclosure(d, root, n + 1)
    assert not merkle.verify_disclosure(d, root, n - 1)


def test_malformed_never_raises():
    _, _, root, discs = _disclosures(3)
    d = discs[0]
    for mutate in (
        lambda x: x.pop("path"),
        lambda x: x.update(key="zz"),
        lambda x: x.update(key=None),
        lambda x: x.update(path="nope"),
        lambda x: x.update(path=[{"side": "L"}]),
        lambda x: x.update(index="0"),
        lambda x: x.update(fact=None),
        lambda x: x["fact"].update(value=True),
    ):
        bad = copy.deepcopy(d)
        mutate(bad)
        assert merkle.verify_disclosure(bad, root, 3) is False
    assert merkle.verify_disclosure(d, "not-hex", 3) is False
    assert merkle.verify_disclosure(d, None, 3) is False  # type: ignore[arg-type]
    assert merkle.verify_disclosure(None, root, 3) is False  # type: ignore[arg-type]


def test_odd_node_promoted_not_duplicated():
    a, b, c = (hashlib.sha256(x).digest() for x in (b"a", b"b", b"c"))
    r3 = merkle.tree_root(merkle.build_tree([a, b, c]))
    r4 = merkle.tree_root(merkle.build_tree([a, b, c, c]))
    assert r3 != r4


def test_internal_node_as_leaf_fails():
    facts, levels, root, discs = _disclosures(4)
    # A second-preimage attempt: present the internal node over leaves 0,1 as a leaf.
    d = copy.deepcopy(discs[0])
    internal = levels[1][0]
    d["path"] = [{"side": "R", "hash": levels[1][1].hex()}]
    d["index"] = 0
    d["fact_count"] = 2
    # The forged key/line would have to hash to `internal` as a leaf; it cannot.
    assert merkle.leaf_hash(bytes.fromhex(d["key"]), d["line"]) != internal
    assert not merkle.verify_disclosure(d, root, 2)
    assert not merkle.verify_disclosure(d, root, 4)
    # Raw node hash is not a valid leaf hash of its own preimage either.
    assert merkle.node_hash(levels[0][0], levels[0][1]) == internal
    assert hashlib.sha256(b"\x00" + levels[0][0] + levels[0][1]).digest() != internal


def test_leaf_differs_from_naive_hash():
    fact = _fact(1)
    line = merkle.canonical_line(fact)
    leaf = merkle.leaf_hash(merkle.leaf_key(SALT, fact["id"]), line)
    assert leaf != hashlib.sha256(SALT + line.encode()).digest()
    assert leaf != hashlib.sha256(line.encode()).digest()


def test_different_salt_different_root():
    facts = _facts(3)
    r1 = merkle.tree_root(merkle.commit_facts(SALT, facts)[0])
    r2 = merkle.tree_root(merkle.commit_facts(bytes(32), facts)[0])
    assert r1 != r2


def test_fact_id_is_bound():
    _, _, root, discs = _disclosures(3)
    bad = copy.deepcopy(discs[0])
    bad["fact"]["id"] = "f:link:LIED:max_flow"
    assert not merkle.verify_disclosure(bad, root, 3)


@pytest.mark.parametrize("small,big", [(3, 4), (7, 8), (9, 10)])
def test_fact_count_is_committed(small, big):
    _, _, root, discs = _disclosures(small)
    d = copy.deepcopy(discs[0])
    assert merkle.verify_disclosure(d, root, small)
    d["fact_count"] = big
    assert not merkle.verify_disclosure(d, root, big)
    assert not merkle.verify_disclosure(d, root, small)


@pytest.mark.parametrize("bad", [0, -1, True, 2.0, "3"])
def test_bad_fact_count_rejected(bad):
    _, _, root, discs = _disclosures(3)
    assert merkle.verify_disclosure(discs[0], root, bad) is False
    with pytest.raises(ValueError):
        merkle.published_root(bytes(32), bad)


def test_numpy_scalars_match_stdlib():
    np = pytest.importorskip("numpy")
    assert merkle.canonical_line(_fact(1, np.float64(14.2))) == merkle.canonical_line(
        _fact(1, 14.2)
    )
    assert merkle.canonical_line(_fact(1, np.float64(14.2))).split("|")[4] == "14.2"
    assert merkle.canonical_line(_fact(1, np.int64(3))) == merkle.canonical_line(_fact(1, 3))


@pytest.mark.parametrize("field", ["scope", "kind", "entity", "attr", "unit", "time"])
def test_pipe_in_field_rejected(field):
    f = _fact(1)
    f[field] = "a|b"
    with pytest.raises(ValueError):
        merkle.canonical_line(f)
