"""Ledger store v2: schema, migration, bitemporal queries, triggers, rtree fallback."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openswmm_mcp.ledger import store as store_mod
from openswmm_mcp.ledger.common import canonical_time
from openswmm_mcp.ledger.store import LedgerError, LedgerStore

# The literal Phase 1 DDL (git f7af70b), user_version 1.
V1_CREATE = """
CREATE TABLE IF NOT EXISTS tallies (
    tally_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    label TEXT NOT NULL,
    root TEXT NOT NULL,
    fact_count INTEGER NOT NULL,
    path TEXT NOT NULL,
    inp_sha256 TEXT NOT NULL,
    sources_root TEXT,
    recorded_at TEXT NOT NULL
)
"""
V1_STAMP = "2026-03-04T05:06:07.123456+00:00"

SHA_A = "a" * 64
SHA_B = "b" * 64
MODEL = "m" * 64


def _store(output_dir: Path) -> LedgerStore:
    return LedgerStore(output_dir / "ledger.db")


def _reg(store: LedgerStore, sid="pipes", sha=SHA_A, **kw):
    return store.register_source(
        source_id=sid, path=f"/data/{sid}.geojson", sha256=sha, role="network",
        format="geojson", crs="local:feet", **kw,
    )  # fmt: skip


def _tally(store: LedgerStore, tid="s/m", root="r1", **kw):
    return store.record_tally(
        tally_id=tid, session_id="s", label="m", root=root, fact_count=3,
        path="/x/m.tally.json", inp_sha256=MODEL, **kw,
    )  # fmt: skip


def test_fresh_db_is_version_2(output_dir):
    s = _store(output_dir)
    assert s.get_tally("nope") is None
    s.ensure()
    raw = sqlite3.connect(s.path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    assert {"sources", "features", "lineage", "mentions", "tallies"} <= names
    raw.close()
    s.close()


def _make_v1(path: Path) -> None:
    raw = sqlite3.connect(path)
    raw.execute(V1_CREATE)
    raw.execute(
        "INSERT INTO tallies VALUES ('s/m','s','m','root1',5,'/x/m.tally.json',?,NULL,?)",
        (MODEL, V1_STAMP),
    )
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()


def test_migration_1_to_2_normalises_time(output_dir):
    db = output_dir / "ledger.db"
    _make_v1(db)
    s = LedgerStore(db)
    row = s.get_tally("s/m")
    assert row is not None
    assert row["recorded_at"] == canonical_time(V1_STAMP) == "2026-03-04T05:06:07.123456Z"
    assert row["root"] == "root1" and row["sources"] is None and row["run_fingerprint"] is None
    assert [r["tally_id"] for r in s.list_tallies()] == ["s/m"]
    raw = sqlite3.connect(db)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='tallies_v1'").fetchone() is None
    raw.close()
    # A recommit on the migrated DB keeps the migrated row in history.
    _tally(s, root="root2")
    assert [h["root"] for h in s.tally_history("s/m")] == ["root1", "root2"]
    s.close()


def test_migration_rolls_back_on_failure(output_dir, monkeypatch):
    db = output_dir / "ledger.db"
    _make_v1(db)

    def boom(self, conn):
        raise RuntimeError("step failed")

    monkeypatch.setattr(LedgerStore, "_create_v2", staticmethod(lambda conn: boom(None, conn)))
    s = LedgerStore(db)
    with pytest.raises(RuntimeError, match="step failed"):
        s.get_tally("s/m")
    raw = sqlite3.connect(db)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
    cols = [r[1] for r in raw.execute("PRAGMA table_info(tallies)")]
    assert cols == [
        "tally_id", "session_id", "label", "root", "fact_count", "path", "inp_sha256",
        "sources_root", "recorded_at",
    ]  # fmt: skip
    assert raw.execute("SELECT root FROM tallies").fetchone()[0] == "root1"
    raw.close()
    monkeypatch.undo()
    assert LedgerStore(db).get_tally("s/m")["root"] == "root1"  # a retry migrates cleanly


def test_newer_schema_refused(output_dir):
    db = output_dir / "ledger.db"
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version = 9")
    raw.close()
    with pytest.raises(RuntimeError, match="newer"):
        LedgerStore(db).list_tallies()


def test_recorded_at_strictly_increasing(output_dir, monkeypatch):
    s = _store(output_dir)
    monkeypatch.setattr(store_mod, "utc_now", lambda: "2026-01-01T00:00:00.000000Z")
    stamps = [_tally(s, tid=f"s/t{i}")["recorded_at"] for i in range(3)]
    assert stamps == sorted(set(stamps)) and len(stamps) == 3
    assert stamps[1] == "2026-01-01T00:00:00.000001Z"
    s.close()


def test_recommit_keeps_history(output_dir):
    s = _store(output_dir)
    a = _tally(
        s, root="r1", run_fingerprint={"k": 1}, sources_root="sr", sources=[{"source_id": "x"}]
    )
    b = _tally(s, root="r2")
    assert a["run_fingerprint"] == {"k": 1} and a["sources"] == [{"source_id": "x"}]
    hist = s.tally_history("s/m")
    assert [h["root"] for h in hist] == ["r1", "r2"]
    assert hist[0]["superseded_at"] == b["recorded_at"] and hist[1]["superseded_at"] is None
    assert s.get_tally("s/m")["root"] == "r2"
    assert [r["root"] for r in s.list_tallies()] == ["r2"]
    # as_known before the recommit sees the first belief
    assert s.current_tally("s", a["recorded_at"])["root"] == "r1"
    assert s.current_tally("s")["root"] == "r2"
    assert s.current_tally("other") is None
    s.close()


def test_update_is_two_rows_and_bitemporal(output_dir):
    s = _store(output_dir)
    row, status = _reg(s, acquired="2026-01-01")
    assert status == "new" and row["valid_from"] == "2026-01-01T00:00:00.000000Z"
    t_first = row["recorded_at"]
    same, status = _reg(s, acquired="2026-01-01")
    assert status == "unchanged" and same["row_id"] == row["row_id"]
    new, status = _reg(s, sha=SHA_B, valid_from="2026-06-01")
    assert status == "updated" and new["sha256"] == SHA_B
    assert new["valid_from"] == "2026-06-01T00:00:00.000000Z"
    raw = sqlite3.connect(s.path)
    rows = raw.execute(
        "SELECT sha256, valid_from, valid_to, superseded_at FROM sources ORDER BY row_id"
    ).fetchall()
    raw.close()
    assert len(rows) == 3
    assert rows[0][3] is not None and rows[0][2] is None  # R superseded
    assert rows[1][:2] == (SHA_A, "2026-01-01T00:00:00.000000Z")
    assert rows[1][2] == "2026-06-01T00:00:00.000000Z" and rows[1][3] is None  # R'
    assert rows[2][0] == SHA_B and rows[2][2] is None  # N

    def shas(as_of, as_known=None):
        return [r["sha256"] for r in s.sources(as_of, as_known)]

    assert shas("2026-03-01") == [SHA_A]
    assert shas("2026-07-01") == [SHA_B]
    assert shas("2025-12-01") == []  # before valid_from
    # known before the update: only the original, open row
    assert shas("2026-07-01", t_first) == [SHA_A]
    assert s.excluded_counts("2025-12-01")["not_yet_valid"] == 2
    assert s.excluded_counts("2026-07-01")["retired"] == 1
    assert s.source_version("pipes", SHA_A)["sha256"] == SHA_A
    assert s.source_version("pipes", SHA_A, as_of="2026-07-01") is None
    with pytest.raises(LedgerError) as err:
        _reg(s, sha="c" * 64, valid_from="2026-02-01")
    assert err.value.slug == "bad_time"
    s.close()


def test_retire_as_known_and_as_of(output_dir):
    s = _store(output_dir)
    row, _ = _reg(s, acquired="2026-01-01")
    out = s.retire_source("pipes", "2026-05-01")
    assert out["valid_to"] == "2026-05-01T00:00:00.000000Z" and out["superseded"] == 1
    assert [r["source_id"] for r in s.sources("2026-04-01")] == ["pipes"]  # true then
    assert s.sources("2026-06-01") == []
    assert [r["valid_to"] for r in s.sources("2026-04-01", row["recorded_at"])] == [None]
    assert len(s.sources("2026-06-01", valid_only=False)) == 1
    assert s.excluded_counts("2026-06-01")["retired"] == 1
    with pytest.raises(LedgerError) as err:
        s.retire_source("pipes")
    assert err.value.slug == "already_retired"
    with pytest.raises(LedgerError) as err:
        s.retire_source("ghost")
    assert err.value.slug == "unknown_source"
    s.close()


def test_retire_before_valid_from_refused(output_dir):
    s = _store(output_dir)
    _reg(s, acquired="2026-03-01")
    with pytest.raises(LedgerError) as err:
        s.retire_source("pipes", "2026-02-01")
    assert err.value.slug == "bad_time"
    assert len(s.sources("2026-04-01")) == 1  # the failed retire wrote nothing
    s.close()


def test_features_lineage_mentions_visibility(output_dir):
    s = _store(output_dir)
    _reg(s, acquired="2026-01-01")
    feats = [
        {"feature_key": "J2", "geom": {"type": "Point", "coordinates": [1, 2]},
         "bbox": (1, 2, 1, 2), "props": {"ID": "J2"}},
        {"feature_key": "far", "geom": {"type": "Point", "coordinates": [900, 900]},
         "bbox": (900, 900, 900, 900), "props": {}},
        {"feature_key": "table", "geom": None, "bbox": None, "props": {"a": 1}},
    ]  # fmt: skip
    assert s.add_features("pipes", SHA_A, feats) == 3
    assert s.add_features("pipes", SHA_A, feats) == 0
    now = "2026-02-01"
    hit = s.features_in_bbox("pipes", SHA_A, (0, 0, 10, 10), None, now)
    assert [f["feature_key"] for f in hit] == ["J2"] and hit[0]["props"] == {"ID": "J2"}
    assert s.features_in_bbox("pipes", SHA_A, (0, 0, 10, 10), "2025-01-01", "2025-06-01") == []
    edge = {
        "element_kind": "node", "element_id": "J2", "model_sha256": MODEL, "source_id": "pipes",
        "source_sha256": SHA_A, "feature_key": "J2", "rule": "id_match", "params": {"key": "ID"},
    }  # fmt: skip
    assert s.add_edges([edge]) == (1, 0)
    assert s.add_edges([edge]) == (0, 1)
    assert len(s.edges(model_sha256=MODEL, as_of=now)) == 1
    lf = s.lineage_features(MODEL, element_kind="node", element_id="J2", as_of=now)
    assert lf[0]["feature"]["feature_key"] == "J2" and lf[0]["edge"]["rule"] == "id_match"
    assert s.sources_for_model(MODEL) == [
        {"source_id": "pipes", "sha256": SHA_A, "role": "network"}
    ]
    assert s.sources_for_model("z" * 64, now) == []
    mention = {"chunk_index": 0, "element_kind": "node", "element_id": "J2",
               "span_start": 5, "span_end": 7, "text": "see J2"}  # fmt: skip
    assert s.add_mentions("pipes", SHA_A, [mention]) == 1
    assert s.add_mentions("pipes", SHA_A, [mention]) == 0
    assert len(s.mentions(element_id="J2", as_of=now)) == 1
    t_before_retire = store_mod.utc_now()
    # Retire: still visible before valid_to, gone after; the input set follows.
    s.retire_source("pipes", "2026-03-01")
    assert len(s.mentions(element_id="J2", as_of="2026-02-15")) == 1
    assert s.mentions(element_id="J2", as_of="2026-04-01") == []
    assert s.edges(model_sha256=MODEL, as_of="2026-04-01") == []
    assert s.features_in_bbox("pipes", SHA_A, (0, 0, 10, 10), None, "2026-04-01") == []
    assert s.sources_for_model(MODEL) == []
    assert len(s.sources_for_model(MODEL, t_before_retire)) == 1  # believed open then
    # An update keeps edges on the old sha attached to the previous version only.
    s2 = _store(output_dir / "second")
    _reg(s2, acquired="2026-01-01")
    s2.add_edges([edge])
    _reg(s2, sha=SHA_B, valid_from="2026-06-01")
    assert s2.sources_for_model(MODEL) == []  # edges stay on the previous version
    old = s2.edges(model_sha256=MODEL, as_of="2026-03-01")
    assert [e["source_sha256"] for e in old] == [SHA_A]
    s2.close()
    s.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE sources SET path = 'x'",
        "DELETE FROM sources",
        "UPDATE sources SET superseded_at = '2030-01-01T00:00:00.000000Z'",  # already set below
        "UPDATE features SET props_json = '{}'",
        "DELETE FROM features",
        "UPDATE lineage SET rule = 'manual'",
        "DELETE FROM lineage",
        "UPDATE mentions SET text = 'x'",
        "DELETE FROM mentions",
        "UPDATE tallies SET root = 'x'",
        "DELETE FROM tallies",
    ],
)
def test_triggers_abort(output_dir, sql):
    s = _store(output_dir)
    _reg(s, acquired="2026-01-01")
    s.retire_source("pipes")  # leaves a superseded row and an open-then-closed current row
    s.add_features("pipes", SHA_A, [{"feature_key": "k", "bbox": (0, 0, 1, 1)}])
    s.add_edges([{
        "element_kind": "node", "element_id": "J2", "model_sha256": MODEL, "source_id": "pipes",
        "source_sha256": SHA_A, "feature_key": "k", "rule": "manual", "params": {},
    }])  # fmt: skip
    s.add_mentions("pipes", SHA_A, [{"chunk_index": 0, "element_kind": "node", "element_id": "J2",
                                      "span_start": 0, "span_end": 2, "text": "J2"}])  # fmt: skip
    _tally(s)
    raw = sqlite3.connect(s.path, isolation_level=None)
    with pytest.raises(sqlite3.DatabaseError, match="append-only|set once"):
        raw.execute(sql)
    raw.close()
    s.close()


def test_superseded_at_set_once_trigger(output_dir):
    s = _store(output_dir)
    _tally(s)
    raw = sqlite3.connect(s.path, isolation_level=None)
    raw.execute("UPDATE tallies SET superseded_at = '2030-01-01T00:00:00.000000Z'")
    with pytest.raises(sqlite3.DatabaseError, match="set once"):
        raw.execute("UPDATE tallies SET superseded_at = '2031-01-01T00:00:00.000000Z'")
    with pytest.raises(sqlite3.DatabaseError, match="set once"):
        raw.execute("UPDATE tallies SET superseded_at = NULL")
    raw.close()
    s.close()


def _spatial_fixture(s: LedgerStore) -> None:
    _reg(s, acquired="2026-01-01")
    s.add_features("pipes", SHA_A, [
        {"feature_key": "near", "bbox": (0, 0, 5, 5), "geom": None},
        {"feature_key": "far", "bbox": (500, 500, 600, 600), "geom": None},
    ])  # fmt: skip


def test_rtree_index_when_available(output_dir):
    s = _store(output_dir)
    s.ensure()
    if s.index != "rtree":
        pytest.skip("SQLite built without R*Tree")
    _spatial_fixture(s)
    assert [f["feature_key"] for f in s.features_in_bbox("pipes", SHA_A, (1, 1, 2, 2))] == ["near"]
    raw = sqlite3.connect(s.path)
    assert raw.execute("SELECT COUNT(*) FROM features_rtree").fetchone()[0] == 2
    raw.execute("DELETE FROM features_rtree")  # a DB first written without rtree
    raw.commit()
    raw.close()
    s.close()
    s2 = LedgerStore(s.path)
    s2.ensure()  # backfill on connect
    raw = sqlite3.connect(s.path)
    assert raw.execute("SELECT COUNT(*) FROM features_rtree").fetchone()[0] == 2
    raw.close()
    assert len(s2.features_in_bbox("pipes", SHA_A, (0, 0, 1000, 1000))) == 2
    s2.close()


def test_rtree_off_uses_bbox_columns(output_dir, monkeypatch):
    monkeypatch.setattr(LedgerStore, "_probe_rtree", lambda self, conn: False)
    s = _store(output_dir)
    s.ensure()
    assert s.index == "bbox"
    _spatial_fixture(s)
    assert [f["feature_key"] for f in s.features_in_bbox("pipes", SHA_A, (1, 1, 2, 2))] == ["near"]
    assert s.features_in_bbox("pipes", SHA_A, (200, 200, 300, 300)) == []
    raw = sqlite3.connect(s.path)
    assert (
        raw.execute("SELECT name FROM sqlite_master WHERE name='features_rtree'").fetchone() is None
    )
    raw.close()
    s.close()


def test_rtree_query_error_drops_to_bbox(output_dir):
    s = _store(output_dir)
    s.ensure()
    if s.index != "rtree":
        pytest.skip("SQLite built without R*Tree")
    _spatial_fixture(s)
    s._conn.execute("DROP TABLE features_rtree")
    assert [f["feature_key"] for f in s.features_in_bbox("pipes", SHA_A, (1, 1, 2, 2))] == ["near"]
    assert s.index == "bbox"
    s.close()


def test_bad_time_input(output_dir):
    s = _store(output_dir)
    with pytest.raises(LedgerError) as err:
        s.sources("not-a-date")
    assert err.value.slug == "bad_time"
    s.close()


def test_predicates_verbatim():
    assert store_mod.KNOWN == (
        "T.recorded_at <= :as_known AND (T.superseded_at IS NULL OR T.superseded_at > :as_known)"
    )
    assert store_mod.known("s") == (
        "s.recorded_at <= :as_known AND (s.superseded_at IS NULL OR s.superseded_at > :as_known)"
    )
    assert store_mod.valid("s") == (
        "(s.valid_from IS NULL OR s.valid_from <= :as_of) AND "
        "(s.valid_to IS NULL OR :as_of < s.valid_to)"
    )
