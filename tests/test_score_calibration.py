"""gold.score_calibration tests (WS1 Epic 1.2, slice 2).

Deterministic, isolated: temp warehouse DuckDB per test, no network, no LLM.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from job_search_toolkit.pipelines.jd.gold import build_score_calibration
from job_search_toolkit.pipelines.jd.silver import ensure_outcomes_table


def _setup_warehouse(db_path: Path, jobs: list[dict], events: list[dict]) -> None:
    """Create minimal silver.jobs + fact_outcome_event and insert rows."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS silver")
        con.execute(
            """
            CREATE TABLE silver.jobs (
                id VARCHAR,
                source_board VARCHAR,
                overall_score DOUBLE,
                scores JSON
            )
            """
        )
        for j in jobs:
            con.execute(
                "INSERT INTO silver.jobs VALUES (?, ?, ?, ?)",
                [j["id"], j["source_board"], j["overall_score"], j["scores"]],
            )
        ensure_outcomes_table(con)
        for e in events:
            con.execute(
                "INSERT INTO silver.fact_outcome_event "
                "(outcome_event_id, job_id, stage, ts, note, provenance) "
                "VALUES (?, ?, ?, ?, NULL, 'test')",
                [e["id"], e["job_id"], e["stage"], e["ts"]],
            )
    finally:
        con.close()


def _query(db_path: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(db_path))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()

def _build_calibration(db_path: Path) -> None:
    """Build just the score_calibration view (needs only jobs + outcomes)."""
    con = duckdb.connect(str(db_path))
    try:
        build_score_calibration(con)
    finally:
        con.close()


def test_score_calibration_no_data(tmp_path: Path):
    db = tmp_path / "warehouse_nodata.db"
    jobs = [
        {"id": "j1", "source_board": "b", "overall_score": 0.5,
         "scores": '{"pay": 0.3, "tech_match": 0.9}'},
        {"id": "j2", "source_board": "b", "overall_score": 0.4,
         "scores": '{"pay": 0.8}'},
    ]
    events = [{"id": "e1", "job_id": "j1", "stage": "applied",
               "ts": "2026-08-01T00:00:00Z"}]
    _setup_warehouse(db, jobs, events)
    _build_calibration(db)

    rows = _query(db, "SELECT * FROM gold.score_calibration")
    # 4 features x 4 bands = 16 rows always emitted
    assert len(rows) == 16
    for r in rows:
        jobs_in_band, applied_count, advanced_count, rate, note = r[3:]
        if jobs_in_band == 0:
            assert applied_count == 0 and advanced_count == 0
            assert rate is None
            assert note == "not enough data"
    # the two populated bands have no fabricated rate either
    rates = _query(
        db,
        "SELECT feature, band_start, advance_rate FROM gold.score_calibration "
        "WHERE jobs_in_band > 0",
    )
    assert rates == [
        ("pay", 0.25, None),
        ("pay", 0.75, None),
        ("tech_match", 0.75, None),
    ]


def test_score_calibration_advance_rate(tmp_path: Path):
    db = tmp_path / "warehouse_signal.db"
    # 6 low tech_match jobs applied, none advanced (below MIN_ADVANCE_COUNT=5
    # threshold is 5; 6 applied -> 'ok'); 6 high tech_match jobs applied, 4 advanced.
    jobs, events = [], []
    for i in range(6):
        jid = f"low-{i}"
        jobs.append({"id": jid, "source_board": "b", "overall_score": 0.2,
                     "scores": '{"tech_match": 0.1}'})
        events.append({"id": f"a-low-{i}", "job_id": jid,
                       "stage": "applied", "ts": "2026-08-01T00:00:00Z"})
    for i in range(6):
        jid = f"high-{i}"
        jobs.append({"id": jid, "source_board": "b", "overall_score": 0.8,
                     "scores": '{"tech_match": 0.9}'})
        events.append({"id": f"a-high-{i}", "job_id": jid,
                       "stage": "applied", "ts": "2026-08-01T00:00:00Z"})
        if i < 4:  # 4 of 6 high-tech jobs advanced
            events.append({"id": f"i-high-{i}", "job_id": jid,
                           "stage": "interview", "ts": "2026-08-05T00:00:00Z"})
    _setup_warehouse(db, jobs, events)
    _build_calibration(db)

    rows = _query(
        db,
        "SELECT band_start, jobs_in_band, applied_count, advanced_count, "
        "advance_rate, confidence_note FROM gold.score_calibration "
        "WHERE feature = 'tech_match' ORDER BY band_start",
    )
    assert rows == [
        (0.0, 6, 6, 0, 0.0, "ok"),
        (0.25, 0, 0, 0, None, "not enough data"),
        (0.5, 0, 0, 0, None, "not enough data"),
        (0.75, 6, 6, 4, 4 / 6, "ok"),
    ]
    high_rate = rows[3][4]
    assert high_rate > rows[0][4]  # higher band advances more


def test_score_calibration_join_nullable(tmp_path: Path):
    db = tmp_path / "warehouse_orphan.db"
    jobs = [{"id": "j1", "source_board": "b", "overall_score": 0.5,
             "scores": '{"tech_match": 0.9}'}]
    events = [
        # orphan outcome: no matching silver.jobs row — must not break the view
        {"id": "e-orphan", "job_id": "folder-no-warehouse-job",
         "stage": "applied", "ts": "2026-08-01T00:00:00Z"},
    ]
    _setup_warehouse(db, jobs, events)
    _build_calibration(db)

    rows = _query(db, "SELECT COUNT(*) FROM gold.score_calibration")
    assert rows[0][0] == 16  # view still builds fully
    # the orphan job contributes to no band
    orphan_rows = _query(
        db,
        "SELECT jobs_in_band FROM gold.score_calibration WHERE jobs_in_band > 0",
    )
    # only tech_match band 0.75-1.0 has j1
    assert len(orphan_rows) == 1 and orphan_rows[0][0] == 1
