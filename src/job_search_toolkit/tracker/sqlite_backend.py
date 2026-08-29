"""SQLite-backed outcome tracker (WS1 Epic 1.1).

Append-only event feed stored in a single sqlite file (stdlib ``sqlite3``,
zero new deps). Repo convention (see pipelines/jd/silver.py): bound ``?``
parameters deadlock on duckdb 1.5.5, so all values are rendered into SQL
via ``sql_literal``. Same style is used here for consistency, even though
sqlite3 itself would accept parameters.

Idempotency: a UNIQUE index on (job_id, stage, ts, COALESCE(note, ''))
makes exact re-records no-ops (SQLite treats NULLs as distinct, hence the
COALESCE). A missing / corrupt DB file is recreated with a UserWarning; an
EMPTY directory squatting on the path is likewise recreated. A NON-empty
directory is never deleted (destructive): warn and raise instead.
"""

from __future__ import annotations

import sqlite3
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_search_toolkit.tracker.protocol import STAGES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    ts          TEXT NOT NULL,
    note        TEXT,
    provenance  TEXT NOT NULL DEFAULT 'sqlite',
    recorded_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_payload
    ON events(job_id, stage, ts, COALESCE(note, ''));
"""


def sql_literal(value: Any) -> str:
    """Render a Python scalar as a SQL literal (silver.py convention).

    None -> NULL; strings single-quoted with embedded quotes doubled.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


class SQLiteTracker:
    """Append-only outcome tracker on a local sqlite file.

    Connections are opened per operation and closed immediately: Windows
    refuses to unlink a file with any open handle, so a persistent
    connection would make corrupt-DB repair impossible while another
    instance is alive.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._open()

    # -- lifecycle ---------------------------------------------------------

    def _open(self) -> None:
        """Create / repair the DB up front.

        Missing file: created silently. Corrupt file: removed and
        recreated with a UserWarning. An empty directory squatting on the
        path is likewise removed and recreated with a warning. A NON-empty
        directory is never deleted (that would destroy user data): warn
        and fail with a descriptive error.
        """
        if self.db_path.is_dir():
            if any(self.db_path.iterdir()):
                warnings.warn(
                    f"tracker DB path {self.db_path} is a non-empty "
                    "directory; refusing to delete it — the path is "
                    "unusable as a tracker DB",
                    UserWarning,
                    stacklevel=2,
                )
                raise RuntimeError(
                    f"tracker DB path {self.db_path} is a non-empty "
                    "directory; it must not be deleted, so it cannot be "
                    "used as a SQLite tracker DB"
                )
            warnings.warn(
                f"empty directory at tracker DB path {self.db_path}; "
                "removing it and recreating the DB file",
                UserWarning,
                stacklevel=2,
            )
            self._destroy_file()
            self._connect().close()
            return
        try:
            self._connect().close()
        except sqlite3.DatabaseError:
            warnings.warn(
                f"tracker DB at {self.db_path} is corrupt or unreadable; "
                "recreating it from scratch",
                UserWarning,
                stacklevel=2,
            )
            self._destroy_file()
            self._connect().close()

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection with the schema applied; caller closes."""
        if self.db_path.is_dir():
            # sqlite3.connect() on a directory path fails opaque-ish
            # ("unable to open database file") on open, not exec. Only
            # reachable from record/current/iter_outcomes on a dir that
            # appeared after construction.
            raise sqlite3.DatabaseError("directory at DB path")
        con = sqlite3.connect(self.db_path)
        try:
            con.executescript(_SCHEMA)
            con.commit()
        except sqlite3.DatabaseError:
            con.close()
            raise
        return con

    def _destroy_file(self) -> None:
        if self.db_path.is_dir():
            self.db_path.rmdir()
        elif self.db_path.exists():
            self.db_path.unlink()

    # -- Tracker protocol --------------------------------------------------

    def record(self, job_id: str, stage: str, ts: str,
               note: str | None = None,
               provenance: str = "sqlite") -> None:
        if stage not in STAGES:
            raise ValueError(
                f"unknown stage {stage!r}; valid stages: {', '.join(STAGES)}"
            )
        recorded_at = datetime.now(UTC).isoformat()
        con = self._connect()
        try:
            # INSERT OR IGNORE: the unique index makes exact re-records no-ops.
            con.execute(
                "INSERT OR IGNORE INTO events (job_id, stage, ts, note, "
                "provenance, recorded_at) VALUES ("
                f"{sql_literal(job_id)}, {sql_literal(stage)}, "
                f"{sql_literal(ts)}, {sql_literal(note)}, "
                f"{sql_literal(provenance)}, "
                f"{sql_literal(recorded_at)})"
            )
            con.commit()
        finally:
            con.close()

    def current(self, job_id: str) -> dict | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT job_id, stage, ts, note, provenance, recorded_at "
                f"FROM events WHERE job_id = {sql_literal(job_id)} "
                "ORDER BY ts DESC, id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        return self._event(row) if row else None

    def iter_outcomes(self) -> list[dict]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT job_id, stage, ts, note, provenance, recorded_at "
                "FROM events ORDER BY id ASC"
            ).fetchall()
        finally:
            con.close()
        return [self._event(r) for r in rows]

    @staticmethod
    def _event(row: tuple) -> dict:
        return {
            "job_id": row[0],
            "stage": row[1],
            "ts": row[2],
            "note": row[3],
            "provenance": row[4],
            "recorded_at": row[5],
        }
