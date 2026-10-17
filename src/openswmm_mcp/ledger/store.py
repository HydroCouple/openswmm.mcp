"""SQLite store of the provenance ledger (``user_version`` 2).

The tally JSON files stay the source of truth for tallies; this store indexes them and holds
the Phase 2 bitemporal tables (sources, features, lineage, mentions). Rows are append-only
(triggers); a belief is replaced by setting ``superseded_at`` once and inserting a new row.
Times are canonical text (``common.canonical_time``). Standard library only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from openswmm_mcp.ledger.common import canonical_time, next_after, utc_now

SCHEMA_VERSION = 2

_log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Bitemporal predicates (design note section 2). ``T`` is replaced by the table alias.
# --------------------------------------------------------------------------------------
KNOWN = "T.recorded_at <= :as_known AND (T.superseded_at IS NULL OR T.superseded_at > :as_known)"
VALID = "(T.valid_from IS NULL OR T.valid_from <= :as_of) AND (T.valid_to IS NULL OR :as_of < T.valid_to)"  # noqa: E501
# Feature / mention visibility: the content version (source_id, sha256) is known and valid.
VERSION_VISIBLE = (
    "EXISTS (SELECT 1 FROM sources s WHERE s.source_id = T.source_id "
    "AND s.sha256 = T.source_sha256 AND "
    + KNOWN.replace("T.", "s.")
    + " AND "
    + VALID.replace("T.", "s.")
    + ")"
)


def known(alias: str) -> str:
    """The *known* predicate for ``alias`` (binds ``:as_known``)."""
    return KNOWN.replace("T.", alias + ".")


def valid(alias: str) -> str:
    """The *valid* predicate for ``alias`` (binds ``:as_of``)."""
    return VALID.replace("T.", alias + ".")


def _row_visible(alias: str) -> str:
    """Features and mentions: ledger time of the row plus a known, valid source version."""
    return f"{alias}.recorded_at <= :as_known AND " + VERSION_VISIBLE.replace("T.", alias + ".")


def _edge_visible(alias: str) -> str:
    """Lineage edges: known(e) AND valid(e) AND the source version is known and valid."""
    return f"{known(alias)} AND {valid(alias)} AND " + VERSION_VISIBLE.replace("T.", alias + ".")


# --------------------------------------------------------------------------------------
# Schema (design note section 7)
# --------------------------------------------------------------------------------------
_ROLES = "'network','nodes','survey','asbuilt','rain','flow','scada','document','model','other'"
_FORMATS = "'geojson','gpkg','shp','csv','inp','pdf','md','txt','json'"

_SOURCES_COLS = (
    "row_id, source_id, path, sha256, role, format, crs, agency, acquired, notes, meta_json, "
    "valid_from, valid_to, recorded_at"
)
_LINEAGE_COLS = (
    "edge_id, element_kind, element_id, model_sha256, source_id, source_sha256, feature_key, "
    "rule, params_json, valid_from, valid_to, recorded_at"
)
_TALLIES_COLS = (
    "row_id, tally_id, session_id, label, root, fact_count, path, inp_sha256, sources_root, "
    "sources_json, run_fingerprint, recorded_at"
)


def _frozen_triggers(table: str, columns: str) -> tuple[str, ...]:
    return (
        f"CREATE TRIGGER {table}_frozen BEFORE UPDATE OF {columns} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'ledger rows are append-only'); END",
        f"CREATE TRIGGER {table}_close_once BEFORE UPDATE OF superseded_at ON {table} "
        "WHEN OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL "
        "BEGIN SELECT RAISE(ABORT, 'superseded_at is set once'); END",
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'ledger rows are append-only'); END",
    )


def _immutable_triggers(table: str) -> tuple[str, ...]:
    return (
        f"CREATE TRIGGER {table}_frozen BEFORE UPDATE ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'ledger rows are append-only'); END",
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'ledger rows are append-only'); END",
    )


_V2_TABLES: tuple[str, ...] = (
    f"""CREATE TABLE sources (
  row_id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL,
  path TEXT NOT NULL, sha256 TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ({_ROLES})),
  format TEXT NOT NULL CHECK (format IN ({_FORMATS})),
  crs TEXT NOT NULL,
  agency TEXT, acquired TEXT, notes TEXT,
  meta_json TEXT NOT NULL DEFAULT '{{}}',
  valid_from TEXT, valid_to TEXT,
  recorded_at TEXT NOT NULL, superseded_at TEXT,
  CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from < valid_to),
  CHECK (superseded_at IS NULL OR superseded_at >= recorded_at)
)""",
    "CREATE INDEX sources_by_id ON sources(source_id, recorded_at)",
    "CREATE INDEX sources_by_sha ON sources(source_id, sha256)",
    "CREATE UNIQUE INDEX sources_current ON sources(source_id, ifnull(valid_from, '')) "
    "WHERE superseded_at IS NULL",
    """CREATE TABLE features (
  fid INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, feature_key TEXT NOT NULL,
  geom_json TEXT,
  bbox_minx REAL, bbox_miny REAL, bbox_maxx REAL, bbox_maxy REAL,
  props_json TEXT NOT NULL DEFAULT '{}',
  recorded_at TEXT NOT NULL,
  UNIQUE (source_id, source_sha256, feature_key)
)""",
    "CREATE INDEX features_by_bbox ON features(source_id, bbox_minx, bbox_maxx)",
    """CREATE TABLE lineage (
  edge_id INTEGER PRIMARY KEY,
  element_kind TEXT NOT NULL CHECK (element_kind IN ('node','link','subcatchment','gage')),
  element_id TEXT NOT NULL, model_sha256 TEXT NOT NULL,
  source_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, feature_key TEXT NOT NULL,
  rule TEXT NOT NULL CHECK (rule IN ('id_match','nearest','manual')),
  params_json TEXT NOT NULL,
  valid_from TEXT, valid_to TEXT, recorded_at TEXT NOT NULL, superseded_at TEXT
)""",
    "CREATE INDEX lineage_by_element ON lineage(model_sha256, element_kind, element_id)",
    "CREATE INDEX lineage_by_source ON lineage(source_id, source_sha256)",
    "CREATE UNIQUE INDEX lineage_current ON lineage(model_sha256, element_kind, element_id, "
    "source_id, source_sha256, feature_key) WHERE superseded_at IS NULL",
    """CREATE TABLE mentions (
  mention_id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, chunk_index INTEGER NOT NULL,
  element_kind TEXT NOT NULL, element_id TEXT NOT NULL,
  span_start INTEGER NOT NULL, span_end INTEGER NOT NULL,
  text TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE (source_id, source_sha256, element_kind, element_id, span_start)
)""",
    "CREATE INDEX mentions_by_element ON mentions(element_kind, element_id)",
    """CREATE TABLE tallies (
  row_id INTEGER PRIMARY KEY,
  tally_id TEXT NOT NULL, session_id TEXT NOT NULL, label TEXT NOT NULL,
  root TEXT NOT NULL, fact_count INTEGER NOT NULL, path TEXT NOT NULL,
  inp_sha256 TEXT NOT NULL,
  sources_root TEXT, sources_json TEXT, run_fingerprint TEXT,
  recorded_at TEXT NOT NULL, superseded_at TEXT
)""",
    "CREATE UNIQUE INDEX tallies_current ON tallies(tally_id) WHERE superseded_at IS NULL",
    "CREATE INDEX tallies_by_session ON tallies(session_id, recorded_at)",
)

_V2_TRIGGERS: tuple[str, ...] = (
    *_frozen_triggers("sources", _SOURCES_COLS),
    *_frozen_triggers("lineage", _LINEAGE_COLS),
    *_frozen_triggers("tallies", _TALLIES_COLS),
    *_immutable_triggers("features"),
    *_immutable_triggers("mentions"),
)

_RTREE_CREATE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS features_rtree USING rtree(id, minx, maxx, miny, maxy)"
)
_RTREE_BACKFILL = (
    "INSERT INTO features_rtree SELECT fid, bbox_minx, bbox_maxx, bbox_miny, bbox_maxy "
    "FROM features WHERE bbox_minx IS NOT NULL AND fid NOT IN (SELECT id FROM features_rtree)"
)

# Phase 1 tally columns (kept in returned rows); the v2 additions follow.
_COLUMNS = (
    "tally_id",
    "session_id",
    "label",
    "root",
    "fact_count",
    "path",
    "inp_sha256",
    "sources_root",
    "recorded_at",
)
_TALLY_INSERT = (
    "tally_id",
    "session_id",
    "label",
    "root",
    "fact_count",
    "path",
    "inp_sha256",
    "sources_root",
    "sources_json",
    "run_fingerprint",
    "recorded_at",
    "superseded_at",
)


class LedgerError(ValueError):
    """A refusable store-level problem; ``slug`` is a stable reason id (e.g. ``bad_time``)."""

    def __init__(self, slug: str, message: str) -> None:
        super().__init__(f"{slug}: {message}")
        self.slug = slug


def _t(value: Any) -> str:
    """Canonical time for a query bound; ``None`` means now."""
    if value is None or value == "":
        return utc_now()
    try:
        return canonical_time(value)  # type: ignore[return-value]
    except ValueError as exc:
        raise LedgerError("bad_time", str(exc)) from exc


def _tn(value: Any) -> str | None:
    """Canonical time or ``None`` (unbounded)."""
    if value is None or value == "":
        return None
    return _t(value)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _tally_row(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in _COLUMNS}
    out["superseded_at"] = row["superseded_at"]
    out["run_fingerprint"] = json.loads(row["run_fingerprint"]) if row["run_fingerprint"] else None
    out["sources"] = json.loads(row["sources_json"]) if row["sources_json"] is not None else None
    return out


def _source_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["meta"] = json.loads(out["meta_json"] or "{}")
    return out


class LedgerStore:
    """Thread-safe provenance store backed by one SQLite file.

    Parameters
    ----------
    path : Path
        Database file. Nothing is created until the first write.

    Attributes
    ----------
    index : str
        ``"rtree"`` or ``"bbox"``: how the spatial prefilter runs (set at connect).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.index = "rtree"
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._last = ""

    # ------------------------------------------------------------------ connection
    def _probe_rtree(self, conn: sqlite3.Connection) -> bool:
        """Create ``features_rtree`` if missing; False when SQLite has no R*Tree module."""
        try:
            conn.execute(_RTREE_CREATE)
        except sqlite3.OperationalError:
            return False
        return True

    def _connect(self, create: bool) -> sqlite3.Connection | None:
        """Return the connection, opening and migrating if needed (caller holds the lock)."""
        if self._conn is not None:
            return self._conn
        if not create and not self.path.exists():
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Ledger database {self.path} has schema version {version}, "
                    f"newer than the supported version {SCHEMA_VERSION}; "
                    "upgrade openswmm-mcp or use a different working directory."
                )
            if version < SCHEMA_VERSION:
                self._migrate(conn)
            self._setup_index(conn)
            self._last = self._max_stamp(conn, all_tables=True)
        except BaseException:
            conn.close()
            raise
        self._conn = conn
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Bring the database to version 2 in one transaction (rolled back on any error)."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:
                self._migrate_1_to_2(conn)
            elif version == 0:
                self._create_v2(conn)
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _create_v2(conn: sqlite3.Connection) -> None:
        for statement in (*_V2_TABLES, *_V2_TRIGGERS):
            conn.execute(statement)

    def _migrate_1_to_2(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT * FROM tallies ORDER BY recorded_at, tally_id").fetchall()
        conn.execute("ALTER TABLE tallies RENAME TO tallies_v1")
        self._create_v2(conn)
        for r in rows:
            conn.execute(
                "INSERT INTO tallies (tally_id, session_id, label, root, fact_count, path, "
                "inp_sha256, sources_root, sources_json, run_fingerprint, recorded_at, "
                "superseded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL)",
                (
                    r["tally_id"],
                    r["session_id"],
                    r["label"],
                    r["root"],
                    r["fact_count"],
                    r["path"],
                    r["inp_sha256"],
                    r["sources_root"],
                    canonical_time(r["recorded_at"]),
                ),
            )
        conn.execute("DROP TABLE tallies_v1")

    def _setup_index(self, conn: sqlite3.Connection) -> None:
        """Probe the rtree; backfill it, or fall back to the bbox columns."""
        if not self._probe_rtree(conn):
            self.index = "bbox"
            _log.warning(
                "SQLite has no R*Tree module; the ledger spatial prefilter uses bbox columns."
            )
            return
        self.index = "rtree"
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(_RTREE_BACKFILL)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _max_stamp(conn: sqlite3.Connection, all_tables: bool = False) -> str:
        tables = ["sources", "lineage", "tallies"]
        if all_tables:
            tables += ["features", "mentions"]
        best = ""
        for table in tables:
            value = conn.execute(f"SELECT MAX(recorded_at) FROM {table}").fetchone()[0]
            if value and value > best:
                best = value
        return best

    def ensure(self) -> None:
        """Open (and create or migrate) the database; makes :attr:`index` meaningful."""
        with self._lock:
            self._connect(create=True)

    # ------------------------------------------------------------------ helpers
    def _write(self, fn: Any) -> Any:
        """Run ``fn(conn, stamp)`` in BEGIN IMMEDIATE .. COMMIT under the lock."""
        with self._lock:
            conn = self._connect(create=True)
            assert conn is not None
            conn.execute("BEGIN IMMEDIATE")
            try:
                last = max(self._last, self._max_stamp(conn))
                now = utc_now()
                stamp = now if now > last else next_after(last)
                result = fn(conn, stamp)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            self._last = stamp
            return result

    def _read(self, fn: Any, default: Any) -> Any:
        with self._lock:
            conn = self._connect(create=False)
            if conn is None:
                return default
            return fn(conn)

    # ------------------------------------------------------------------ sources
    def register_source(
        self,
        *,
        source_id: str,
        path: str,
        sha256: str,
        role: str,
        format: str,
        crs: str,
        agency: str | None = None,
        acquired: str | None = None,
        notes: str | None = None,
        meta: dict[str, Any] | None = None,
        valid_from: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Register a source file version.

        ``valid_from`` is the world time ``t`` (default: ``acquired`` for a new source, now
        for an update). Same sha256 as the open row: nothing is written (``"unchanged"``).
        A new sha256 supersedes the open row R and inserts R' (R with ``valid_to = t``) and
        N (the new version, ``valid_from = t``).

        Returns
        -------
        tuple
            ``(row, status)`` with status ``"new"``, ``"updated"`` or ``"unchanged"``; row is
            the open (current) version.

        Raises
        ------
        LedgerError
            ``bad_time`` for an unparsable time or ``t`` not after the open row's start.
        """
        t_given = _tn(valid_from) or _tn(acquired)
        meta_json = _json(meta or {})

        def op(conn: sqlite3.Connection, stamp: str) -> tuple[dict[str, Any], str]:
            cols = (
                "source_id, path, sha256, role, format, crs, agency, acquired, notes, meta_json, "
                "valid_from, valid_to, recorded_at"
            )

            def insert(vals: tuple[Any, ...]) -> int:
                cur = conn.execute(
                    f"INSERT INTO sources ({cols}) VALUES ({', '.join('?' * 13)})", vals
                )
                return int(cur.lastrowid)

            def fetch(row_id: int) -> dict[str, Any]:
                return _source_row(
                    conn.execute("SELECT * FROM sources WHERE row_id = ?", (row_id,)).fetchone()
                )

            current = conn.execute(
                "SELECT * FROM sources WHERE source_id = ? AND superseded_at IS NULL "
                "ORDER BY row_id",
                (source_id,),
            ).fetchall()
            open_rows = [r for r in current if r["valid_to"] is None]
            new_vals = lambda vf: (  # noqa: E731
                source_id, str(path), sha256, role, format, crs, agency, acquired, notes,
                meta_json, vf, None, stamp,
            )  # fmt: skip
            if not current:
                return fetch(insert(new_vals(t_given))), "new"
            if not open_rows:
                # Retired source registered again: a fresh open row, after the last end.
                t = t_given or stamp
                last_end = max(r["valid_to"] for r in current)
                if t < last_end:
                    raise LedgerError(
                        "bad_time",
                        f"valid_from {t} is before the retired version ended ({last_end})",
                    )
                return fetch(insert(new_vals(t))), "updated"
            r = open_rows[-1]
            if r["sha256"] == sha256:
                return _source_row(r), "unchanged"
            t = t_given or stamp
            if r["valid_from"] is not None and t <= r["valid_from"]:
                raise LedgerError(
                    "bad_time",
                    f"the new version's start {t} is not after the current version's "
                    f"valid_from {r['valid_from']}",
                )
            conn.execute(
                "UPDATE sources SET superseded_at = ? WHERE row_id = ?", (stamp, r["row_id"])
            )
            insert(
                (
                    r["source_id"],
                    r["path"],
                    r["sha256"],
                    r["role"],
                    r["format"],
                    r["crs"],
                    r["agency"],
                    r["acquired"],
                    r["notes"],
                    r["meta_json"],
                    r["valid_from"],
                    t,
                    stamp,
                )  # fmt: skip
            )
            return fetch(insert(new_vals(t))), "updated"

        return self._write(op)

    def retire_source(self, source_id: str, valid_to: str | None = None) -> dict[str, Any]:
        """End a source's world validity at ``valid_to`` (default now).

        Supersedes the open row and inserts a copy with ``valid_to`` set, so
        ``as_known`` before this call still sees the source open.

        Raises
        ------
        LedgerError
            ``unknown_source``, ``already_retired`` or ``bad_time``.
        """
        end = _tn(valid_to)

        def op(conn: sqlite3.Connection, stamp: str) -> dict[str, Any]:
            rows = conn.execute(
                "SELECT * FROM sources WHERE source_id = ? AND superseded_at IS NULL "
                "ORDER BY row_id",
                (source_id,),
            ).fetchall()
            if not rows:
                raise LedgerError("unknown_source", f"no source {source_id!r}")
            open_rows = [r for r in rows if r["valid_to"] is None]
            if not open_rows:
                raise LedgerError("already_retired", f"source {source_id!r} is already retired")
            r = open_rows[-1]
            to = end or stamp
            if r["valid_from"] is not None and to <= r["valid_from"]:
                raise LedgerError(
                    "bad_time", f"valid_to {to} is not after valid_from {r['valid_from']}"
                )
            conn.execute(
                "UPDATE sources SET superseded_at = ? WHERE row_id = ?", (stamp, r["row_id"])
            )
            cur = conn.execute(
                "INSERT INTO sources (source_id, path, sha256, role, format, crs, agency, "
                "acquired, notes, meta_json, valid_from, valid_to, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["source_id"], r["path"], r["sha256"], r["role"], r["format"], r["crs"],
                    r["agency"], r["acquired"], r["notes"], r["meta_json"], r["valid_from"],
                    to, stamp,
                ),
            )  # fmt: skip
            row = conn.execute(
                "SELECT * FROM sources WHERE row_id = ?", (cur.lastrowid,)
            ).fetchone()
            return {
                "source_id": source_id,
                "valid_to": to,
                "recorded_at": stamp,
                "superseded": 1,
                "row": _source_row(row),
            }

        return self._write(op)

    def sources(
        self,
        as_of: str | None = None,
        as_known: str | None = None,
        roles: list[str] | None = None,
        *,
        valid_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Source rows known at ``as_known``; valid at ``as_of`` unless ``valid_only=False``."""
        params: dict[str, Any] = {"as_of": _t(as_of), "as_known": _t(as_known)}
        sql = f"SELECT s.* FROM sources s WHERE {known('s')}"
        if valid_only:
            sql += f" AND {valid('s')}"
        sql += self._role_clause(roles, params, "s")
        sql += " ORDER BY s.source_id, s.sha256"
        return self._read(lambda c: [_source_row(r) for r in c.execute(sql, params).fetchall()], [])

    @staticmethod
    def _role_clause(roles: list[str] | None, params: dict[str, Any], alias: str) -> str:
        if not roles:
            return ""
        names = []
        for i, role in enumerate(roles):
            params[f"role{i}"] = role
            names.append(f":role{i}")
        return f" AND {alias}.role IN ({', '.join(names)})"

    def excluded_counts(
        self,
        as_of: str | None = None,
        as_known: str | None = None,
        roles: list[str] | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, int]:
        """Counts of known source versions in scope that are ``not_yet_valid`` or ``retired``."""
        params: dict[str, Any] = {"as_of": _t(as_of), "as_known": _t(as_known)}
        scope = self._role_clause(roles, params, "s")
        if source_ids:
            names = []
            for i, sid in enumerate(source_ids):
                params[f"sid{i}"] = sid
                names.append(f":sid{i}")
            scope += f" AND s.source_id IN ({', '.join(names)})"
        base = f"SELECT COUNT(*) FROM sources s WHERE {known('s')}{scope}"
        sql_future = base + " AND s.valid_from > :as_of"
        sql_retired = base + " AND s.valid_to <= :as_of"
        return self._read(
            lambda c: {
                "not_yet_valid": c.execute(sql_future, params).fetchone()[0],
                "retired": c.execute(sql_retired, params).fetchone()[0],
            },
            {"not_yet_valid": 0, "retired": 0},
        )

    def source_version(
        self,
        source_id: str,
        sha256: str,
        as_known: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any] | None:
        """The latest row for ``(source_id, sha256)`` known at ``as_known`` (and valid at
        ``as_of`` when given), or ``None``."""
        params: dict[str, Any] = {
            "as_of": _t(as_of),
            "as_known": _t(as_known),
            "sid": source_id,
            "sha": sha256,
        }
        sql = (
            "SELECT s.* FROM sources s WHERE s.source_id = :sid AND s.sha256 = :sha "
            f"AND {known('s')}"
        )
        if as_of is not None:
            sql += f" AND {valid('s')}"
        sql += " ORDER BY s.recorded_at DESC, s.row_id DESC LIMIT 1"

        def op(c: sqlite3.Connection) -> dict[str, Any] | None:
            row = c.execute(sql, params).fetchone()
            return _source_row(row) if row else None

        return self._read(op, None)

    # ------------------------------------------------------------------ features
    def add_features(self, source_id: str, sha256: str, rows: list[dict[str, Any]]) -> int:
        """Insert features of a source content version; existing keys are skipped.

        Each row: ``feature_key``, ``geom`` (GeoJSON geometry dict or ``None``), ``bbox``
        ``(minx, miny, maxx, maxy)`` or ``None``, ``props`` (dict). Returns the number added.
        """

        def op(conn: sqlite3.Connection, stamp: str) -> int:
            added = 0
            for f in rows:
                bbox = f.get("bbox")
                geom = f.get("geom")
                cur = conn.execute(
                    "INSERT OR IGNORE INTO features (source_id, source_sha256, feature_key, "
                    "geom_json, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, props_json, "
                    "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_id,
                        sha256,
                        str(f["feature_key"]),
                        _json(geom) if geom is not None else None,
                        *(tuple(float(v) for v in bbox) if bbox else (None,) * 4),
                        _json(f.get("props") or {}),
                        stamp,
                    ),
                )
                if cur.rowcount != 1:
                    continue
                added += 1
                if bbox and self.index == "rtree":
                    minx, miny, maxx, maxy = (float(v) for v in bbox)
                    conn.execute(
                        "INSERT INTO features_rtree (id, minx, maxx, miny, maxy) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (cur.lastrowid, minx, maxx, miny, maxy),
                    )
            return added

        return self._write(op)

    @staticmethod
    def _feature(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source_id": row["source_id"],
            "source_sha256": row["source_sha256"],
            "feature_key": row["feature_key"],
            "geom": json.loads(row["geom_json"]) if row["geom_json"] else None,
            "bbox": (
                (row["bbox_minx"], row["bbox_miny"], row["bbox_maxx"], row["bbox_maxy"])
                if row["bbox_minx"] is not None
                else None
            ),
            "props": json.loads(row["props_json"]),
            "recorded_at": row["recorded_at"],
        }

    def features_in_bbox(
        self,
        source_id: str,
        sha256: str,
        bbox: tuple[float, float, float, float],
        as_known: str | None = None,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        """Visible features of one source version whose bbox meets ``(minx, miny, maxx, maxy)``."""
        params: dict[str, Any] = {
            "as_of": _t(as_of),
            "as_known": _t(as_known),
            "sid": source_id,
            "sha": sha256,
            "minx": bbox[0],
            "miny": bbox[1],
            "maxx": bbox[2],
            "maxy": bbox[3],
        }
        visible = _row_visible("f")
        tail = f" AND f.source_sha256 = :sha AND {visible} ORDER BY f.feature_key"
        rtree_sql = (
            "SELECT f.* FROM features f JOIN features_rtree r ON r.id = f.fid "
            "WHERE f.source_id = :sid AND r.maxx >= :minx AND r.minx <= :maxx "
            "AND r.maxy >= :miny AND r.miny <= :maxy" + tail
        )
        bbox_sql = (
            "SELECT f.* FROM features f WHERE f.source_id = :sid AND f.bbox_maxx >= :minx "
            "AND f.bbox_minx <= :maxx AND f.bbox_maxy >= :miny AND f.bbox_miny <= :maxy" + tail
        )

        def op(c: sqlite3.Connection) -> list[dict[str, Any]]:
            if self.index == "rtree":
                try:
                    return [self._feature(r) for r in c.execute(rtree_sql, params).fetchall()]
                except sqlite3.OperationalError:
                    self.index = "bbox"
                    _log.warning("ledger rtree query failed; using bbox columns for this session")
            return [self._feature(r) for r in c.execute(bbox_sql, params).fetchall()]

        return self._read(op, [])

    # ------------------------------------------------------------------ lineage
    def add_edges(self, edges: list[dict[str, Any]]) -> tuple[int, int]:
        """Insert lineage edges; an identical current edge is skipped.

        Each edge: ``element_kind``, ``element_id``, ``model_sha256``, ``source_id``,
        ``source_sha256``, ``feature_key``, ``rule``, ``params`` (dict), optional
        ``valid_from`` / ``valid_to``. Returns ``(added, skipped)``.
        """
        prepared = [(e, _tn(e.get("valid_from")), _tn(e.get("valid_to"))) for e in edges]

        def op(conn: sqlite3.Connection, stamp: str) -> tuple[int, int]:
            added = 0
            for e, vf, vt in prepared:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO lineage (element_kind, element_id, model_sha256, "
                    "source_id, source_sha256, feature_key, rule, params_json, valid_from, "
                    "valid_to, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        e["element_kind"],
                        e["element_id"],
                        e["model_sha256"],
                        e["source_id"],
                        e["source_sha256"],
                        str(e["feature_key"]),
                        e["rule"],
                        _json(e.get("params") or {}),
                        vf,
                        vt,
                        stamp,
                    ),
                )
                added += cur.rowcount
            return added, len(prepared) - added

        return self._write(op)

    @staticmethod
    def _edge(row: sqlite3.Row) -> dict[str, Any]:
        out = {k: row[k] for k in row.keys() if k not in ("params_json",)}
        out["params"] = json.loads(row["params_json"])
        return out

    def edges(
        self,
        *,
        model_sha256: str | None = None,
        element_kind: str | None = None,
        element_id: str | None = None,
        source_id: str | None = None,
        as_of: str | None = None,
        as_known: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lineage edges visible at ``as_of`` / ``as_known`` (edge and source version both)."""
        params: dict[str, Any] = {"as_of": _t(as_of), "as_known": _t(as_known)}
        sql = f"SELECT e.* FROM lineage e WHERE {_edge_visible('e')}"
        for col, value in (
            ("model_sha256", model_sha256),
            ("element_kind", element_kind),
            ("element_id", element_id),
            ("source_id", source_id),
        ):
            if value is not None:
                params[col] = value
                sql += f" AND e.{col} = :{col}"
        sql += " ORDER BY e.element_kind, e.element_id, e.source_id, e.feature_key, e.edge_id"
        return self._read(lambda c: [self._edge(r) for r in c.execute(sql, params)], [])

    def lineage_features(
        self,
        model_sha256: str,
        *,
        element_kind: str | None = None,
        element_id: str | None = None,
        as_of: str | None = None,
        as_known: str | None = None,
    ) -> list[dict[str, Any]]:
        """Visible edges joined to their (visible) features: ``{"edge": ..., "feature": ...}``."""
        params: dict[str, Any] = {
            "as_of": _t(as_of),
            "as_known": _t(as_known),
            "model_sha256": model_sha256,
        }
        sql = (
            "SELECT e.edge_id AS e_edge_id, e.* , f.geom_json, f.bbox_minx, f.bbox_miny, "
            "f.bbox_maxx, f.bbox_maxy, f.props_json AS f_props_json, "
            "f.recorded_at AS f_recorded_at FROM lineage e JOIN features f "
            "ON f.source_id = e.source_id AND f.source_sha256 = e.source_sha256 "
            "AND f.feature_key = e.feature_key "
            f"WHERE e.model_sha256 = :model_sha256 AND {_edge_visible('e')} "
            f"AND {_row_visible('f')}"
        )
        for col, value in (("element_kind", element_kind), ("element_id", element_id)):
            if value is not None:
                params[col] = value
                sql += f" AND e.{col} = :{col}"
        sql += " ORDER BY e.element_kind, e.element_id, e.source_id, e.feature_key, e.edge_id"

        def op(c: sqlite3.Connection) -> list[dict[str, Any]]:
            out = []
            for r in c.execute(sql, params).fetchall():
                edge = self._edge(r)
                for k in ("e_edge_id", "geom_json", "bbox_minx", "bbox_miny", "bbox_maxx",
                          "bbox_maxy", "f_props_json", "f_recorded_at"):  # fmt: skip
                    edge.pop(k, None)
                feature = {
                    "source_id": r["source_id"],
                    "source_sha256": r["source_sha256"],
                    "feature_key": r["feature_key"],
                    "geom": json.loads(r["geom_json"]) if r["geom_json"] else None,
                    "bbox": (
                        (r["bbox_minx"], r["bbox_miny"], r["bbox_maxx"], r["bbox_maxy"])
                        if r["bbox_minx"] is not None
                        else None
                    ),
                    "props": json.loads(r["f_props_json"]),
                    "recorded_at": r["f_recorded_at"],
                }
                out.append({"edge": edge, "feature": feature})
            return out

        return self._read(op, [])

    # ------------------------------------------------------------------ mentions
    def add_mentions(self, source_id: str, sha256: str, rows: list[dict[str, Any]]) -> int:
        """Insert document mentions; duplicates are skipped. Returns the number added.

        Each row: ``chunk_index``, ``element_kind``, ``element_id``, ``span_start``,
        ``span_end``, ``text``.
        """

        def op(conn: sqlite3.Connection, stamp: str) -> int:
            added = 0
            for m in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO mentions (source_id, source_sha256, chunk_index, "
                    "element_kind, element_id, span_start, span_end, text, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_id,
                        sha256,
                        int(m["chunk_index"]),
                        m["element_kind"],
                        m["element_id"],
                        int(m["span_start"]),
                        int(m["span_end"]),
                        m["text"],
                        stamp,
                    ),
                )
                added += cur.rowcount
            return added

        return self._write(op)

    def mentions(
        self,
        *,
        element_kind: str | None = None,
        element_id: str | None = None,
        source_id: str | None = None,
        as_of: str | None = None,
        as_known: str | None = None,
    ) -> list[dict[str, Any]]:
        """Mentions whose source version is known and valid, and recorded by ``as_known``."""
        params: dict[str, Any] = {"as_of": _t(as_of), "as_known": _t(as_known)}
        sql = f"SELECT m.* FROM mentions m WHERE {_row_visible('m')}"
        for col, value in (
            ("element_kind", element_kind),
            ("element_id", element_id),
            ("source_id", source_id),
        ):
            if value is not None:
                params[col] = value
                sql += f" AND m.{col} = :{col}"
        sql += " ORDER BY m.source_id, m.source_sha256, m.span_start, m.mention_id"
        return self._read(lambda c: [dict(r) for r in c.execute(sql, params).fetchall()], [])

    # ------------------------------------------------------------------ tallies
    def record_tally(
        self,
        *,
        tally_id: str,
        session_id: str,
        label: str,
        root: str,
        fact_count: int,
        path: str,
        inp_sha256: str,
        sources_root: str | None = None,
        run_fingerprint: dict[str, Any] | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Record a tally; a current row with the same ``tally_id`` is superseded (kept).

        Returns
        -------
        dict
            The stored row: the Phase 1 columns (``recorded_at`` is canonical UTC text) plus
            ``superseded_at``, ``run_fingerprint`` and ``sources``.
        """

        def op(conn: sqlite3.Connection, stamp: str) -> dict[str, Any]:
            conn.execute(
                "UPDATE tallies SET superseded_at = ? WHERE tally_id = ? AND superseded_at IS NULL",
                (stamp, tally_id),
            )
            conn.execute(
                f"INSERT INTO tallies ({', '.join(_TALLY_INSERT)}) "
                f"VALUES ({', '.join('?' * len(_TALLY_INSERT))})",
                (
                    tally_id,
                    session_id,
                    label,
                    root,
                    int(fact_count),
                    str(path),
                    inp_sha256,
                    sources_root,
                    _json(sources) if sources is not None else None,
                    _json(run_fingerprint) if run_fingerprint is not None else None,
                    stamp,
                    None,
                ),
            )
            return _tally_row(
                conn.execute(
                    "SELECT * FROM tallies WHERE tally_id = ? AND superseded_at IS NULL",
                    (tally_id,),
                ).fetchone()
            )

        return self._write(op)

    def get_tally(self, tally_id: str) -> dict[str, Any] | None:
        """Return the current row for ``tally_id``, or ``None`` if absent."""

        def op(c: sqlite3.Connection) -> dict[str, Any] | None:
            row = c.execute(
                "SELECT * FROM tallies WHERE tally_id = ? AND superseded_at IS NULL", (tally_id,)
            ).fetchone()
            return _tally_row(row) if row else None

        return self._read(op, None)

    def list_tallies(self) -> list[dict[str, Any]]:
        """Return current rows, newest first, ties broken by ``tally_id``."""
        return self._read(
            lambda c: [
                _tally_row(r)
                for r in c.execute(
                    "SELECT * FROM tallies WHERE superseded_at IS NULL "
                    "ORDER BY recorded_at DESC, tally_id"
                ).fetchall()
            ],
            [],
        )

    def tally_history(self, tally_id: str) -> list[dict[str, Any]]:
        """Every row ever recorded for ``tally_id``, oldest first."""
        return self._read(
            lambda c: [
                _tally_row(r)
                for r in c.execute(
                    "SELECT * FROM tallies WHERE tally_id = ? ORDER BY recorded_at, row_id",
                    (tally_id,),
                ).fetchall()
            ],
            [],
        )

    def current_tally(self, session_id: str, as_known: str | None = None) -> dict[str, Any] | None:
        """The session's most recently recorded tally row known at ``as_known``, or ``None``."""
        params = {"as_known": _t(as_known), "session_id": session_id}
        sql = (
            f"SELECT * FROM tallies t WHERE t.session_id = :session_id AND {known('t')} "
            "ORDER BY t.recorded_at DESC, t.row_id DESC LIMIT 1"
        )

        def op(c: sqlite3.Connection) -> dict[str, Any] | None:
            row = c.execute(sql, params).fetchone()
            return _tally_row(row) if row else None

        return self._read(op, None)

    def sources_for_model(self, model_sha256: str, at: str | None = None) -> list[dict[str, str]]:
        """The ``sources_root`` input set at ``at`` (design note section 5).

        Distinct ``(source_id, sha256, role)`` of source rows known and valid at ``at`` that
        have at least one lineage edge known and valid at ``at`` for this model, ordered by
        ``source_id`` then ``sha256``.
        """
        stamp = _t(at)
        params = {"as_of": stamp, "as_known": stamp, "model": model_sha256}
        sql = (
            "SELECT DISTINCT s.source_id, s.sha256, s.role FROM sources s "
            f"WHERE {known('s')} AND {valid('s')} AND EXISTS (SELECT 1 FROM lineage e "
            "WHERE e.model_sha256 = :model AND e.source_id = s.source_id "
            f"AND e.source_sha256 = s.sha256 AND {known('e')} AND {valid('e')}) "
            "ORDER BY s.source_id, s.sha256"
        )
        return self._read(
            lambda c: [
                {"source_id": r["source_id"], "sha256": r["sha256"], "role": r["role"]}
                for r in c.execute(sql, params).fetchall()
            ],
            [],
        )

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
