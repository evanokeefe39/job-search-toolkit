"""Bridge export assets: materialize consumer files from silver.jobs.

The jd-refresh / new-application skills read files by path. Until their
Phase 4 updates land (they land in the same branch), the pipeline must keep
producing them — these assets are the backward-compat bridge: exports
materialized from the warehouse on every run, never the source of truth.
"""

import duckdb
import dagster as dg
from dagster import AssetExecutionContext

from .score import scored_jobs
from ..config import SILVER_DIR, WAREHOUSE_DB


def _export_json(con, select_sql: str, path) -> None:
    """COPY a SELECT to a JSON array file (DuckDB serializes JSON columns nested)."""
    sql = f"COPY ({select_sql}) TO '{path.as_posix()}' (ARRAY true)"
    con.execute(sql)


@dg.asset(
    deps=[scored_jobs],
    group_name="exports",
    description="Export active silver jobs to merged_jobs.json (backward-compat bridge)",
)
def merged_jobs_export(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Export the canonical lookup file the new-application skill reads."""
    path = SILVER_DIR / "merged_jobs.json"
    with duckdb.connect(str(WAREHOUSE_DB)) as con:
        _export_json(
            con,
            "SELECT * FROM silver.jobs WHERE is_active ORDER BY id, source_board",
            path,
        )
    return dg.MaterializeResult(metadata={"path": str(path)})


@dg.asset(
    deps=[scored_jobs],
    group_name="exports",
    description="Export active freework jobs to freework_jobs_enriched.json (backward-compat bridge)",
)
def freework_enriched_export(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Export the freework-only enriched file (fallback lookup for new-application)."""
    path = SILVER_DIR / "freework_jobs_enriched.json"
    with duckdb.connect(str(WAREHOUSE_DB)) as con:
        _export_json(
            con,
            "SELECT * FROM silver.jobs WHERE is_active AND source_board = 'freework' "
            "ORDER BY id",
            path,
        )
    return dg.MaterializeResult(metadata={"path": str(path)})
