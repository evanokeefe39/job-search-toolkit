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
from dataclasses import dataclass

import dagster as dg
import duckdb
from dagster import AssetExecutionContext, InitResourceContext

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


# ---------------------------------------------------------------------------
# Resume-from-bronze: ingest an explicit run id (optionally one board)
# ---------------------------------------------------------------------------

def _manifest() -> list[dict]:
    """The bronze manifest (``runs.json``) as a list of entries.

    A run that scraped always writes a manifest, so a missing manifest means
    nothing has ever been scraped — a hard error for ingest (nothing to read).
    """
    if not BRONZE_RUNS.exists():
        raise ValueError(
            f"bronze manifest {BRONZE_RUNS} missing — run the scrape assets first"
        )
    return json.loads(BRONZE_RUNS.read_text(encoding="utf-8"))


def list_runs() -> list[dict]:
    """All bronze manifest entries, or ``[]`` when the manifest is missing.

    Used by ``pipeline ingest --list-runs`` to show available run ids and
    their per-board job counts so a user can pick an orphaned snapshot.
    """
    if not BRONZE_RUNS.exists():
        return []
    return json.loads(BRONZE_RUNS.read_text(encoding="utf-8"))


def ingest_bronze(
    con: duckdb.DuckDBPyConnection, run_id: str, board: str | None = None
) -> int:
    """Upsert an explicit run's bronze (all boards, or one board) into silver.jobs.

    Reads ``runs.json`` for ``run_id``, loads the matching bronze files and
    upserts them into the warehouse (idempotent, enrichment-preserving — the
    same ``ON CONFLICT`` semantics as the per-board ``silver_<board>`` assets).
    Used to recover a bronze snapshot that was scraped but never ingested
    (e.g. a run that died after scraping) WITHOUT re-scraping.

    Validation (mirrors the plan's edge-case inventory):

    - unknown ``run_id`` → ``ValueError`` listing the available run ids.
    - ``board`` given but absent from the run → ``ValueError`` listing the
      run's boards.
    - a manifest entry whose bronze file is missing on disk → ``ValueError``
      naming the file.

    A board present in the run's manifest with 0 jobs is a valid no-op: the
    run is recorded (its manifest entry exists) and nothing is upserted —
    ``ensure_jobs_table(con, [])`` would CREATE a jobs table whose PK columns
    no empty run provides, so the guard matches the per-board factory.

    Returns the number of jobs ingested.
    """
    manifest = _manifest()
    entries = [e for e in manifest if e.get("run_id") == run_id]
    if not entries:
        runs = sorted({e.get("run_id") for e in manifest})
        raise ValueError(
            f"Unknown run_id {run_id!r}. Available runs: {runs}"
        )
    if board is not None:
        board_entries = [e for e in entries if e.get("board") == board]
        if not board_entries:
            boards = sorted({e.get("board") for e in entries})
            raise ValueError(
                f"Board {board!r} not found in run {run_id!r}. "
                f"Run boards: {boards}"
            )
        entries = board_entries

    jobs: list[dict] = []
    for entry in entries:
        path = BRONZE_DIR / entry["file"]
        if not path.exists():
            raise ValueError(
                f"Bronze file missing for run {run_id!r}, "
                f"board {entry.get('board')!r}: {path}"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        jobs.extend(data)

    ensure_dims(con)
    if jobs:
        columns = ensure_jobs_table(con, jobs)
        upsert_run(con, run_id, jobs, columns)
        refresh_dim_date(con)
    return len(jobs)


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


@dataclass
class IngestConfig:
    """Which bronze run to ingest via ``silver_ingest`` (optional board subset).

    ``run_id`` is the ``runs.json`` run id of an orphaned bronze snapshot;
    ``board`` narrows the ingest to a single board (``None`` = all boards).
    On the normal ``pipeline run`` path neither is set, so ``silver_ingest``
    is a safe no-op (it is a dependency of ``scored_jobs`` only so the ingest
    job can order score/export/gold after it).
    """

    run_id: str | None = None
    board: str | None = None


@dg.resource(config_schema={
    "run_id": dg.Field(str, is_required=False, default_value=""),
    "board": dg.Field(str, is_required=False, default_value=""),
})
def ingest_config(context: InitResourceContext) -> IngestConfig:
    """Dagster resource exposing the ``IngestConfig`` for ``silver_ingest``.

    Built from resource config so the ingest CLI can inject an explicit
    run id/board via ``run_config``; defaults to an empty (no-op) config on
    the normal ranking path.
    """
    cfg = context.resource_config
    return IngestConfig(
        run_id=(cfg.get("run_id") or None),
        board=(cfg.get("board") or None),
    )


@dg.asset(
    resource_defs={"ingest": ingest_config},
    group_name="processing",
    description=(
        "Upsert an explicit run's bronze (optionally one board) into "
        "silver.jobs without re-scraping — resume-from-bronze recovery"
    ),
)
def silver_ingest(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Recover an orphaned bronze snapshot: ingest an explicit run_id.

    Reads ``context.resources.ingest.run_id`` (and optional ``board``) and
    upserts that run's bronze into ``silver.jobs``. This is the ingest-only
    recovery path — it never runs a scrape asset. On the normal ``pipeline
    run`` path ``run_id`` is unset and this is a no-op, so score/export/gold
    still flow unchanged.
    """
    cfg = context.resources.ingest
    run_id = cfg.run_id
    if not run_id:
        return dg.MaterializeResult(metadata={
            "ingested": 0,
            "board": cfg.board or "all",
            "run_id": "",
            "note": "no ingest run_id configured — no-op",
        })

    with connect() as con:
        ingested = ingest_bronze(con, run_id, cfg.board)
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
        "ingested": ingested,
        "board": cfg.board or "all",
        "run_id": run_id,
        "warehouse_total": total,
    })
