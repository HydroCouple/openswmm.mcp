"""SQLite index of recorded tallies.

The tally JSON files are the source of truth; :class:`LedgerStore` is a thin
index over them so ``list`` works across sessions. Phase 2 adds tables and
migrates ``user_version`` from 1 to 2.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

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

_CREATE = """
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


class LedgerStore:
    """Thread-safe tally index backed by one SQLite file.

    Parameters
    ----------
    path : Path
        Database file. Nothing is created until the first use.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _connect(self, create: bool) -> sqlite3.Connection | None:
        """Return the connection, opening it if needed (caller holds the lock)."""
        if self._conn is not None:
            return self._conn
        if not create and not self.path.exists():
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Ledger database {self.path} has schema version {version}, "
                    f"newer than the supported version {SCHEMA_VERSION}; "
                    "upgrade openswmm-mcp or use a different working directory."
                )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE)
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except BaseException:
            conn.close()
            raise
        self._conn = conn
        return conn

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
    ) -> dict[str, Any]:
        """Insert or replace a tally row and return it.

        Returns
        -------
        dict
            The stored row, including the store-assigned ``recorded_at`` (UTC ISO).
        """
        recorded_at = datetime.now(timezone.utc).isoformat()
        values = (
            tally_id,
            session_id,
            label,
            root,
            int(fact_count),
            str(path),
            inp_sha256,
            sources_root,
            recorded_at,
        )
        with self._lock:
            conn = self._connect(create=True)
            assert conn is not None
            conn.execute(
                f"INSERT OR REPLACE INTO tallies ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                values,
            )
            conn.commit()
        return dict(zip(_COLUMNS, values))

    def get_tally(self, tally_id: str) -> dict[str, Any] | None:
        """Return the row for ``tally_id``, or ``None`` if absent."""
        with self._lock:
            conn = self._connect(create=False)
            if conn is None:
                return None
            row = conn.execute("SELECT * FROM tallies WHERE tally_id = ?", (tally_id,)).fetchone()
        return dict(row) if row else None

    def list_tallies(self) -> list[dict[str, Any]]:
        """Return all rows, newest first, ties broken by ``tally_id``."""
        with self._lock:
            conn = self._connect(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM tallies ORDER BY recorded_at DESC, tally_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
