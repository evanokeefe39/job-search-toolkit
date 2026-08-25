"""Silver upsert asset: ingest the current run's bronze scrapes into DuckDB.

Replaces the old ``merged_jobs`` asset (JSON union + overwrite). The
warehouse table keeps every job ever seen — see silver.py for the schema
and upsert semantics.
"""

import json

import dagster as dg
from dagster import AssetExecutionContext

from .common import BRONZE_RUNS
from .scrape import (
    englishjobs_jobs,
    faruse_jobs,
    freework_jobs,
    hellowork_jobs,
    hiringcafe_jobs,
    linkedin_jobs,
    linkedin_posts,
    remoteok_jobs,
    wwr_jobs,
)
from ..config import BRONZE_DIR
from ..silver import (
    connect,
    ensure_dims,
    ensure_jobs_table,
    refresh_dim_date,
    upsert_run,
)


def _read_bronze_entries(run_id: str) -> list[dict]:
    """Manifest entries for this Dagster run (all boards, one run id)."""
    if not BRONZE_RUNS.exists():
        raise ValueError(
            f"bronze manifest {BRONZE_RUNS} missing — run the scrape assets first"
        )
    manifest = json.loads(BRONZE_RUNS.read_text(encoding="utf-8"))
    entries = [e for e in manifest if e.get("run_id") == run_id]
    if not entries:
        raise ValueError(
            f"no bronze manifest entries for run {run_id} — the scrape assets "
            "did not record this run"
        )
    return entries


@dg.asset(
    deps=[
        freework_jobs,
        hiringcafe_jobs,
        hellowork_jobs,
        englishjobs_jobs,
        faruse_jobs,
        wwr_jobs,
        remoteok_jobs,
        linkedin_jobs,
        linkedin_posts,
    ],
    group_name="processing",
    description="Upsert current-run bronze jobs into silver.jobs (DuckDB warehouse)",
)
def silver_upsert(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Ingest this run's scraped jobs into the warehouse.

    Reads the timestamped bronze files recorded in ``runs.json`` for the
    current Dagster run and upserts them (preserving enrichment on re-scrape).
    Jobs are never deactivated: a subset run (``--boards``) safely ingests
    only the boards it scraped, and staleness is inferred downstream from
    ``last_seen_at`` rather than a global is_active flip.
    """
    run_id = context.run_id
    jobs: list[dict] = []
    for entry in _read_bronze_entries(run_id):
        path = BRONZE_DIR / entry["file"]
        data = json.loads(path.read_text(encoding="utf-8"))
        jobs.extend(data)

    with connect() as con:
        # Dims first: ensure_dims creates them and runs the one-time legacy
        # company_info migration before upsert_run writes dim_company.
        ensure_dims(con)
        columns = ensure_jobs_table(con, jobs)
        upsert_run(con, run_id, jobs, columns)
        refresh_dim_date(con)
        total = con.execute("SELECT COUNT(*) FROM silver.jobs").fetchone()[0]

    return dg.MaterializeResult(metadata={
        "ingested": len(jobs),
        "warehouse_total": total,
        "run_id": run_id,
    })
