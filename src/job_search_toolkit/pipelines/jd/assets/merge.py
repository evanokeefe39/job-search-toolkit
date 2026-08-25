"""Per-board silver assets: ingest each board's bronze scrapes into DuckDB.

Replaces the old composite ``silver_upsert`` (one all-board asset) with one
asset per board, each ingesting only its own board's bronze. A single board's
scrape failure now blocks only that board's ``silver_<board>`` asset — the
others still flow to ``scored_jobs``/gold — and a retry can target just the
failed board via ``--boards``.

The warehouse table keeps every job ever seen — see silver.py for the schema
and idempotent, enrichment-preserving upsert semantics (``ON CONFLICT ...
DO UPDATE`` refreshes only ``last_seen*``/``is_active``/``updated_at``).
"""

import json

import dagster as dg
from dagster import AssetExecutionContext

from .common import BRONZE_RUNS
from .scrape import BOARD_SCRAPE_ASSETS
from ..config import BRONZE_DIR
from ..silver import (
    connect,
    ensure_dims,
    ensure_jobs_table,
    refresh_dim_date,
    upsert_run,
)


def _read_board_bronze(run_id: str, board: str) -> list[dict]:
    """Manifest entries for one board in this run.

    Reads ``runs.json`` for entries whose ``run_id`` and ``board`` match, then
    returns the jobs from each entry's bronze file. When the board is absent
    from the run's manifest (e.g. its scrape failed or simply didn't run),
    returns ``[]`` instead of raising — a per-board reader must not error the
    way the old all-board ``_read_bronze_entries`` did. The manifest file
    itself is still required to exist (a run that scraped always writes one).
    """
    if not BRONZE_RUNS.exists():
        raise ValueError(
            f"bronze manifest {BRONZE_RUNS} missing — run the scrape assets first"
        )
    manifest = json.loads(BRONZE_RUNS.read_text(encoding="utf-8"))
    entries = [
        e for e in manifest
        if e.get("run_id") == run_id and e.get("board") == board
    ]
    jobs: list[dict] = []
    for entry in entries:
        path = BRONZE_DIR / entry["file"]
        data = json.loads(path.read_text(encoding="utf-8"))
        jobs.extend(data)
    return jobs


def make_silver_asset(board: str, scrape_dep) -> dg.AssetsDefinition:
    """Build a ``silver_<board>`` asset that ingests only that board's bronze.

    ``scrape_dep`` is the board's scrape asset (from ``BOARD_SCRAPE_ASSETS``),
    so ``silver_<board>`` runs only after its own scrape and is independent of
    every other board's. Reads only this board's bronze for the current run
    and upserts it (idempotent, enrichment-preserving). ``upsert_run`` with an
    empty list returns early, so a board that scraped 0 jobs still records the
    run without erroring.
    """
    @dg.asset(
        name=f"silver_{board}",
        deps=[scrape_dep],
        group_name="processing",
        description=(
            f"Upsert current-run {board} bronze jobs into silver.jobs "
            "(DuckDB warehouse)"
        ),
    )
    def _silver(context: AssetExecutionContext) -> dg.MaterializeResult:
        """Ingest this board's scraped jobs into the warehouse.

        Reads only this board's timestamped bronze files recorded in
        ``runs.json`` for the current Dagster run and upserts them (preserving
        enrichment on re-scrape). Jobs are never deactivated: a subset run
        (``--boards``) safely ingests only the boards it scraped, and staleness
        is inferred downstream from ``last_seen_at`` rather than a global
        ``is_active`` flip.
        """
        run_id = context.run_id
        jobs = _read_board_bronze(run_id, board)

        with connect() as con:
            # Dims first: ensure_dims creates them and runs the one-time legacy
            # company_info migration before upsert_run writes dim_company.
            ensure_dims(con)
            if jobs:
                # Guard on non-empty jobs: ensure_jobs_table(con, []) would
                # CREATE a table whose PRIMARY KEY references id/source_board
                # that no empty run provides. An empty bronze entry is a valid
                # no-op (the scrape already recorded the run in runs.json) and
                # must not error.
                columns = ensure_jobs_table(con, jobs)
                upsert_run(con, run_id, jobs, columns)
                refresh_dim_date(con)
            table_exists = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'silver' AND table_name = 'jobs'"
            ).fetchone()[0]
            total = (
                con.execute("SELECT COUNT(*) FROM silver.jobs").fetchone()[0]
                if table_exists
                else 0
            )

        return dg.MaterializeResult(metadata={
            "ingested": len(jobs),
            "board": board,
            "warehouse_total": total,
            "run_id": run_id,
        })

    return _silver


# One silver asset per board scrape. datasciencejobs is included here so
# `--boards datasciencejobs` can ingest it, but it is kept off the default
# ranking path (RANKING_ASSETS) — see definitions.py.
SILVER_BOARD_ASSETS: dict[str, dg.AssetsDefinition] = {
    board: make_silver_asset(board, scrape_asset)
    for board, scrape_asset in BOARD_SCRAPE_ASSETS.items()
}
