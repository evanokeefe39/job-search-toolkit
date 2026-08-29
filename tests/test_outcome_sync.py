"""Outcome sync tests (WS1 Epic 1.2, slice 1).

Deterministic, isolated: temp tracker DB + temp warehouse DuckDB, no
network, no live Twenty, no LLM.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from job_search_toolkit.pipelines.jd.assets.outcomes import sync_outcomes
from job_search_toolkit.pipelines.jd.silver import ensure_outcomes_table
from job_search_toolkit.tracker import SQLiteTracker


@pytest.fixture()
def tracker_db(tmp_path: Path) -> Path:
    return tmp_path / "tracker.db"


@pytest.fixture()
def warehouse_db(tmp_path: Path) -> Path:
    return tmp_path / "warehouse.db"


def _fact_count(warehouse_db: Path) -> int:
    con = duckdb.connect(str(warehouse_db))
    try:
        return con.execute(
            "SELECT count(*) FROM silver.fact_outcome_event"
        ).fetchone()[0]
    finally:
        con.close()


def test_outcome_sync_to_warehouse(tmp_path, tracker_db, warehouse_db):
    tracker = SQLiteTracker(db_path=tracker_db)
    tracker.record("job-1", "applied", "2026-08-29T10:00:00Z", note="via site")
    assert tracker.iter_outcomes(), "tracker should hold 1 event"

    con = duckdb.connect(str(warehouse_db))
    try:
        n = sync_outcomes(con, tracker_db)
        assert n == 1
        assert _fact_count(warehouse_db) == 1
        # Idempotent: re-running the sync over the same events inserts nothing.
        n2 = sync_outcomes(con, tracker_db)
        assert n2 == 0
        assert _fact_count(warehouse_db) == 1
    finally:
        con.close()


def test_outcome_sync_missing_tracker_db(tmp_path, warehouse_db):
    con = duckdb.connect(str(warehouse_db))
    try:
        missing = tmp_path / "nope.db"
        n = sync_outcomes(con, missing)
        assert n == 0
        assert not (tmp_path / "nope.db").exists()
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = 'fact_outcome_event'"
        ).fetchone()
        assert row[0] == 0  # nothing created on a no-op
    finally:
        con.close()


def test_outcome_sync_no_matching_job_still_recorded(tmp_path, tracker_db, warehouse_db):
    tracker = SQLiteTracker(db_path=tracker_db)
    tracker.record("folder-no-warehouse-job", "applied", "2026-08-29T11:30:00Z")
    con = duckdb.connect(str(warehouse_db))
    try:
        ensure_outcomes_table(con)
        n = sync_outcomes(con, tracker_db)
        assert n == 1
        con2_row = con.execute(
            "SELECT job_id, stage, note, provenance FROM silver.fact_outcome_event"
        ).fetchone()
        assert con2_row[0] == "folder-no-warehouse-job"
        assert con2_row[1] == "applied"
        assert con2_row[2] is None
        assert con2_row[3] == "sqlite"
    finally:
        con.close()
