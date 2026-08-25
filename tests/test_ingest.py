"""Tests for resume-from-bronze ingest (ingest_bronze + silver_ingest asset).

Covers the plan's behavioral contracts:
- ingest-all: a run's whole bronze (all boards) lands in silver.jobs
- ingest-one-board: only the chosen board's rows land; others untouched
- unknown run id -> error listing the available runs
- board absent from a valid run -> error listing the run's boards
- missing bronze file -> error naming the file
- idempotent re-ingest: no duplicate rows, enrichment preserved (ON CONFLICT)
- empty-board no-op: a board that scraped 0 jobs upserts nothing, no error
- silver_ingest asset materialization via the IngestConfig resource

Run: uv run python -m pytest tests/test_ingest.py -q
"""

from __future__ import annotations

import json

import pytest


def _sjob(jid: str, board: str) -> dict:
    """A minimal canonical job row (enough for ensure_jobs_table/upsert_run)."""
    return {
        "id": jid,
        "source_board": board,
        "title": "Data Engineer",
        "description_text": "text",
        "description_language": "en",
        "technologies": ["Python"],
        "company": "Acme",
        "company_info": {"name": "Acme", "org_type": None},
        "apply_url": f"https://x/{jid}",
        "date_posted": "2026-08-01",
        "salary": {
            "min_annual_eur": 60000.0, "max_annual_eur": 80000.0,
            "currency_original": "EUR", "frequency_original": "yearly",
            "is_disclosed": True,
        },
        "workplace_type": "remote",
        "contract_types": ["contract"],
        "contract_duration": None,
        "location_raw": "Paris",
        "engagement_type": "consulting",
        "posting_company_type": "end_client",
        "end_client_name": None,
        "end_client_sector": None,
        "views": 10,
        "applications": 2,
        "is_expired": False,
        "years_experience_min": 3,
    }


@pytest.fixture
def bronze_wh(tmp_path, monkeypatch):
    """A throwaway bronze manifest + DuckDB warehouse for the ingest tests."""
    from job_search_toolkit.pipelines.jd import silver as S
    from job_search_toolkit.pipelines.jd.assets import merge as M

    bronze = tmp_path / "bronze"
    bronze.mkdir()
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(M, "BRONZE_DIR", bronze)
    monkeypatch.setattr(M, "BRONZE_RUNS", bronze / "runs.json")
    monkeypatch.setattr(S, "WAREHOUSE_DB", db)
    yield bronze, db


def _add_bronze(bronze, run_id: str, board: str, jobs: list[dict]) -> dict:
    """Write a bronze snapshot for (run_id, board); return its manifest entry."""
    file = f"{board}/{run_id}.json"
    path = bronze / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jobs), encoding="utf-8")
    return {"run_id": run_id, "board": board, "timestamp": "T",
            "file": file, "job_count": len(jobs)}


def _write_runs(bronze, entries: list[dict]) -> None:
    (bronze / "runs.json").write_text(json.dumps(entries), encoding="utf-8")


RUN_1 = "11111111-1111-1111-1111-111111111111"
RUN_2 = "22222222-2222-2222-2222-222222222222"


def _connect(bronze_wh):
    from job_search_toolkit.pipelines.jd import silver as S

    _, db = bronze_wh
    return S.connect(), db


def _rows(bronze_wh) -> list[tuple]:
    from job_search_toolkit.pipelines.jd import silver as S

    _, db = bronze_wh
    con = S.connect()
    try:
        exists = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'silver' AND table_name = 'jobs'"
        ).fetchone()[0]
        if not exists:
            return []
        cols = {r[1] for r in con.execute("PRAGMA table_info('silver.jobs')").fetchall()}
        if "id" not in cols:
            return []
        return con.execute("SELECT id, source_board FROM silver.jobs ORDER BY id").fetchall()
    finally:
        con.close()


def _ingest(bronze_wh, run_id: str, board: str | None = None) -> int:
    """Call ingest_bronze against the fixture's warehouse; return count."""
    from job_search_toolkit.pipelines.jd.assets.merge import ingest_bronze

    con, _ = _connect(bronze_wh)
    try:
        return ingest_bronze(con, run_id, board)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# ingest_bronze: behavioral contracts
# ---------------------------------------------------------------------------

def test_ingest_all_boards(bronze_wh):
    """ingest_bronze(run, board=None) lands every board's bronze for the run."""
    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
        _add_bronze(bronze, RUN_1, "hellowork", [_sjob("y1", "hellowork")]),
        _add_bronze(bronze, RUN_2, "freework", [_sjob("x2", "freework")]),
    ])
    count = _ingest(bronze_wh, RUN_1)
    assert count == 2
    assert _rows(bronze_wh) == [("x1", "freework"), ("y1", "hellowork")]


def test_ingest_one_board(bronze_wh):
    """ingest_bronze(run, board=X) ingests only X's bronze; others untouched."""
    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
        _add_bronze(bronze, RUN_1, "hellowork", [_sjob("y1", "hellowork")]),
    ])
    count = _ingest(bronze_wh, RUN_1, "freework")
    assert count == 1
    assert _rows(bronze_wh) == [("x1", "freework")]


def test_ingest_unknown_run_lists_available(bronze_wh):
    """Unknown run id raises ValueError listing the available runs."""
    from job_search_toolkit.pipelines.jd.assets.merge import ingest_bronze

    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
        _add_bronze(bronze, RUN_2, "freework", [_sjob("x2", "freework")]),
    ])
    con, _ = _connect(bronze_wh)
    try:
        with pytest.raises(ValueError) as e:
            ingest_bronze(con, "00000000-0000-0000-0000-000000000000")
    finally:
        con.close()
    assert RUN_1 in str(e.value)
    assert RUN_2 in str(e.value)


def test_ingest_board_absent_lists_run_boards(bronze_wh):
    """Board absent from a valid run raises listing the run's boards."""
    from job_search_toolkit.pipelines.jd.assets.merge import ingest_bronze

    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
    ])
    con, _ = _connect(bronze_wh)
    try:
        with pytest.raises(ValueError) as e:
            ingest_bronze(con, RUN_1, "hellowork")
    finally:
        con.close()
    assert "freework" in str(e.value)
    assert "hellowork" in str(e.value)


def test_ingest_missing_bronze_file_errors(bronze_wh):
    """A manifest entry whose bronze file is gone raises naming the file."""
    from job_search_toolkit.pipelines.jd.assets.merge import ingest_bronze

    bronze, _ = bronze_wh
    # Entry references a file that was never written.
    _write_runs(bronze, [
        {"run_id": RUN_1, "board": "freework", "timestamp": "T",
         "file": "freework/missing.json", "job_count": 1},
    ])
    con, _ = _connect(bronze_wh)
    try:
        with pytest.raises(ValueError) as e:
            ingest_bronze(con, RUN_1)
    finally:
        con.close()
    assert "missing.json" in str(e.value)


def test_ingest_empty_board_noops(bronze_wh):
    """A board that scraped 0 jobs upserts nothing and does not error."""
    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", []),
    ])
    count = _ingest(bronze_wh, RUN_1, "freework")
    assert count == 0
    assert _rows(bronze_wh) == []


def test_ingest_idempotent_preserves_enrichment(bronze_wh):
    """Re-ingesting an already-seen run must not clobber enrichment columns."""
    from job_search_toolkit.pipelines.jd import silver as S

    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
    ])
    _ingest(bronze_wh, RUN_1)

    con, _ = _connect(bronze_wh)
    con.execute(
        "UPDATE silver.jobs SET technologies='[\"Spark\"]'::JSON, "
        "enriched_at=NOW() WHERE id='x1'"
    )
    con.close()

    # Re-ingest the same run id -> ON CONFLICT dedups, enrichment survives.
    count = _ingest(bronze_wh, RUN_1)
    assert count == 1
    con, _ = _connect(bronze_wh)
    row = con.execute(
        "SELECT first_seen_run, last_seen_run, is_active, "
        "enriched_at IS NOT NULL, json_extract_string(technologies, '$[0]'), "
        "(SELECT COUNT(*) FROM silver.jobs WHERE id='x1') "
        "FROM silver.jobs WHERE id='x1'"
    ).fetchone()
    con.close()
    # One row only (no duplicate), enrichment preserved.
    assert row == (RUN_1, RUN_1, True, True, "Spark", 1)


def test_ingest_missing_manifest_errors(bronze_wh):
    """No runs.json at all -> ingest raises a clear error."""
    from job_search_toolkit.pipelines.jd.assets.merge import ingest_bronze

    bronze, _ = bronze_wh  # runs.json never written
    assert not (bronze / "runs.json").exists()
    con, _ = _connect(bronze_wh)
    try:
        with pytest.raises(ValueError) as e:
            ingest_bronze(con, RUN_1)
    finally:
        con.close()
    assert "runs.json" in str(e.value)


# ---------------------------------------------------------------------------
# silver_ingest asset: materializes via the IngestConfig resource
# ---------------------------------------------------------------------------

def _materialize_silver_ingest(run_id: str, board: str | None = None) -> dict:
    """Materialize the silver_ingest asset with an IngestConfig run_id."""
    import dagster as dg

    from job_search_toolkit.pipelines.jd.assets.merge import silver_ingest

    run_config = {
        "resources": {
            "ingest": {"config": {"run_id": run_id, "board": board or ""}}
        }
    }
    result = dg.materialize([silver_ingest], run_config=run_config)
    assert result.success
    mat = result.asset_materializations_for_node("silver_ingest")
    metadata = mat[0].metadata
    return {k: v.value for k, v in metadata.items()}


def test_silver_ingest_asset_ingests_run(bronze_wh):
    """silver_ingest asset with an IngestConfig run_id lands the run's rows."""
    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
        _add_bronze(bronze, RUN_1, "hellowork", [_sjob("y1", "hellowork")]),
    ])
    meta = _materialize_silver_ingest(RUN_1)
    assert meta["ingested"] == 2
    assert meta["run_id"] == RUN_1
    assert _rows(bronze_wh) == [("x1", "freework"), ("y1", "hellowork")]


def test_silver_ingest_asset_ingests_one_board(bronze_wh):
    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
        _add_bronze(bronze, RUN_1, "hellowork", [_sjob("y1", "hellowork")]),
    ])
    meta = _materialize_silver_ingest(RUN_1, "freework")
    assert meta["ingested"] == 1
    assert _rows(bronze_wh) == [("x1", "freework")]


# ---------------------------------------------------------------------------
# CLI: pipeline ingest / pipeline list-runs
# ---------------------------------------------------------------------------

def _run_cli(args, bronze_wh, monkeypatch):
    """Run the CLI pipeline subcommand against the fixture's bronze/warehouse."""
    from job_search_toolkit.pipelines.jd.assets import merge as M
    from job_search_toolkit.pipelines.jd import silver as S
    from typer.testing import CliRunner

    from job_search_toolkit.cli import app

    _, db = bronze_wh
    monkeypatch.setattr(M, "BRONZE_DIR", bronze_wh[0])
    monkeypatch.setattr(M, "BRONZE_RUNS", bronze_wh[0] / "runs.json")
    monkeypatch.setattr(S, "WAREHOUSE_DB", db)
    return CliRunner().invoke(app, ["pipeline", *args])


def test_cli_list_runs_output_shape(bronze_wh, monkeypatch):
    """`pipeline list-runs` prints run ids + per-board job counts."""
    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
        _add_bronze(bronze, RUN_1, "hellowork", [_sjob("y1", "hellowork")]),
        _add_bronze(bronze, RUN_2, "freework", [_sjob("x2", "freework")]),
    ])
    res = _run_cli(["list-runs"], bronze_wh, monkeypatch)
    assert res.exit_code == 0
    assert f"{RUN_1}: freework=1, hellowork=1" in res.output
    assert f"{RUN_2}: freework=1" in res.output


def test_cli_list_runs_empty_manifest(bronze_wh, monkeypatch):
    """`pipeline list-runs` with no manifest prints a clear message."""
    res = _run_cli(["list-runs"], bronze_wh, monkeypatch)
    assert res.exit_code == 0
    assert "No bronze runs found" in res.output


def test_cli_ingest_unknown_run_lists_runs(bronze_wh, monkeypatch):
    """`pipeline ingest --run-id <unknown>` errors listing available runs."""
    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
    ])
    res = _run_cli(["ingest", "--run-id", "00000000-0000-0000-0000-000000000000"],
                   bronze_wh, monkeypatch)
    assert res.exit_code != 0
    assert "Unknown run_id" in res.output
    assert RUN_1 in res.output


def test_cli_ingest_unknown_board_lists_boards(bronze_wh, monkeypatch):
    """`pipeline ingest --run-id R --board <absent>` errors listing run boards."""
    bronze, _ = bronze_wh
    _write_runs(bronze, [
        _add_bronze(bronze, RUN_1, "freework", [_sjob("x1", "freework")]),
    ])
    res = _run_cli(["ingest", "--run-id", RUN_1, "--board", "hellowork"],
                   bronze_wh, monkeypatch)
    assert res.exit_code != 0
    assert "not found in run" in res.output
    assert "freework" in res.output
