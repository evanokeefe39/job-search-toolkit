"""Bridge export assets: materialize consumer files from silver.jobs.

The jd-refresh / new-application skills read files by path. Until their
Phase 4 updates land (they land in the same branch), the pipeline must keep
producing them — these assets are the backward-compat bridge: exports
materialized from the warehouse on every run, never the source of truth.

The fact table no longer stores a ``company_info`` JSON column — the bridge
reconstructs it per row from the golden ``dim_company`` row (same CompanyInfo
shape), so downstream consumers keep reading ``job["company_info"]``
unchanged. Post golden-record dedup there is one dim row per real company, so
every job of a company gets identical enrichment (no per-board drift).
"""

import duckdb
import dagster as dg
from dagster import AssetExecutionContext

from .score import scored_jobs
from ..config import SILVER_DIR, WAREHOUSE_DB

# Rebuild the legacy company_info JSON object from the dim_company join.
# Field set matches schemas.py CompanyInfo; NULLs stay NULL inside the object.
# NOTE: duckdb 1.5.5 json_object takes positional key/value pairs, not the
# Postgres 'key': value form — verified empirically (Parser Error otherwise).
_COMPANY_INFO_JSON = (
    "json_object("
    "'name', c.display_name, "
    "'industry', COALESCE(c.industry, '[]'::JSON), "
    "'size_employees', c.size_employees, "
    "'year_founded', c.year_founded, "
    "'hq_country', c.hq_country, "
    "'org_type', c.org_type, "
    "'stock_symbol', c.stock_symbol, "
    "'stock_exchange', c.stock_exchange, "
    "'latest_funding_type', c.latest_funding_type, "
    "'latest_funding_amount_usd', c.latest_funding_amount_usd, "
    "'homepage_url', c.homepage_url"
    ") AS company_info"
)

_FACT_SELECT = (
    f"SELECT j.*, {_COMPANY_INFO_JSON} "
    f"FROM silver.jobs j LEFT JOIN silver.dim_company c "
    f"ON j.company_id = c.company_id"
)


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
            f"{_FACT_SELECT} WHERE j.is_active ORDER BY j.id, j.source_board",
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
            f"{_FACT_SELECT} WHERE j.is_active AND j.source_board = 'freework' "
            "ORDER BY j.id",
            path,
        )
    return dg.MaterializeResult(metadata={"path": str(path)})
