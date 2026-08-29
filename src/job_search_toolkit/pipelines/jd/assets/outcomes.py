"""Outcome sync asset: land tracker events in the warehouse (WS1 Epic 1.2).

The append-only outcome feed lives in ``data/tracker.db`` (see
job_search_toolkit.tracker). This asset copies every event into the DuckDB
warehouse as a first-class fact table (``silver.fact_outcome_event``) so
gold analytics (e.g. score calibration) can join outcomes against jobs.
Purely deterministic/offline: no LLM, no network — it belongs on the
ranking path, before gold.
"""


from datetime import UTC, datetime
from pathlib import Path

import dagster as dg
from dagster import AssetExecutionContext

from job_search_toolkit.tracker.sqlite_backend import SQLiteTracker

from ..silver import connect, ensure_outcomes_table, outcome_event_id, sql_literal

# Default tracker location (repo-relative, same convention as tracker/__init__).
DEFAULT_TRACKER_DB = Path("data/tracker.db")


def sync_outcomes(con, tracker_db_path: Path) -> int:
    """Sync all tracker events into ``silver.fact_outcome_event``.

    Recomputes the deterministic SHA-1 surrogate key and uses
    ``INSERT ... ON CONFLICT DO NOTHING`` — re-running over the same
    tracker events is a no-op. A missing or empty tracker DB yields 0.
    Returns the number of rows actually inserted.
    """
    if not tracker_db_path.exists():
        return 0
    events = SQLiteTracker(db_path=tracker_db_path).iter_outcomes()
    if not events:
        return 0

    ensure_outcomes_table(con)
    synced_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    before = con.execute("SELECT count(*) FROM silver.fact_outcome_event").fetchone()[0]
    for ev in events:
        eid = outcome_event_id(
            ev["job_id"], ev["stage"], ev["ts"],
            ev.get("note"), ev.get("provenance", "sqlite"),
        )
        con.execute(
            "INSERT INTO silver.fact_outcome_event "
            "(outcome_event_id, job_id, stage, ts, note, provenance, "
            "recorded_at, synced_at) VALUES "
            f"({sql_literal(eid)}, {sql_literal(ev['job_id'])}, "
            f"{sql_literal(ev['stage'])}, {sql_literal(ev['ts'])}, "
            f"{sql_literal(ev.get('note'))}, "
            f"{sql_literal(ev.get('provenance', 'sqlite'))}, "
            f"{sql_literal(ev['recorded_at'])}, '{synced_at}') "
            "ON CONFLICT DO NOTHING"
        )
    after = con.execute("SELECT count(*) FROM silver.fact_outcome_event").fetchone()[0]
    return int(after - before)


@dg.asset(
    deps=[],
    group_name="analytics",
    description="Sync tracker outcome events into silver.fact_outcome_event",
)
def warehouse_outcomes(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Land tracker events as a first-class warehouse fact table (idempotent)."""
    with connect() as con:
        n = sync_outcomes(con, DEFAULT_TRACKER_DB)
    return dg.MaterializeResult(metadata={"synced": n})
