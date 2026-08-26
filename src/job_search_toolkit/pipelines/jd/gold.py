"""DuckDB gold layer: analytics views over the silver.jobs warehouse table.

Views live in the ``gold`` schema of the warehouse database
(``data/warehouse/jobs.db``) and are ``CREATE OR REPLACE`` on every run:

- ``ranked_jobs``            — scored, non-stale jobs ordered by overall_score DESC;
  exposes ``days_since_posted`` / ``days_since_seen`` so shortlisting can apply
  its own freshness judgment on top of the score
- ``by_sector`` / ``by_tier`` — non-stale job counts grouped by sector / tier
- ``job_history``            — full time-series: first/last seen, is_active,
  days active, days since last seen
- ``weekly_snapshot``        — active job counts per ISO week (reconstructed from
  the first/last_seen interval)
- ``new_this_run``           — jobs whose first_seen_run is the given run
- ``disappeared_this_run``   — jobs whose ``last_seen_at`` is older than the
  staleness horizon (``STALE_AFTER_DAYS``) — "likely gone" without a binary
  active/inactive deactivation

Jobs are never deactivated: ``silver.jobs.is_active`` stays TRUE once seen,
and staleness is inferred from time since last seen. This makes subset runs
safe (a board omitted from a run is not flagged as gone — it just stops
refreshing ``last_seen_at`` and eventually falls out of the non-stale views).

``new_this_run`` needs a run id baked in as a literal (a view cannot take
parameters). ``build_gold`` takes it explicitly; the ``gold_views`` asset
passes the current Dagster run id, the ``pipeline gold`` CLI resolves the
most recent run by ``last_seen_at``.

Usage:
    from job_search_toolkit.pipelines.jd.gold import build_gold
    build_gold(Path("data/warehouse/jobs.db"), run_id="run-xyz")
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .silver import STALE_AFTER_DAYS, sql_literal

# Integer days since a job was last seen (never NULL — last_seen_at is set on
# every upsert). Used to gate the non-stale views and define "disappeared".
_DAYS_SINCE_SEEN = (
    "CAST(DATEDIFF('day', CAST(last_seen_at AS DATE), CURRENT_DATE) AS INTEGER)"
)
_NOT_STALE = f"{_DAYS_SINCE_SEEN} <= {STALE_AFTER_DAYS}"
_STALE = f"{_DAYS_SINCE_SEEN} > {STALE_AFTER_DAYS}"


def latest_run(con: duckdb.DuckDBPyConnection) -> str | None:
    """Run id of the most recent scrape (by last_seen_at), if any rows exist."""
    row = con.execute(
        """
        SELECT last_seen_run FROM silver.jobs
        WHERE last_seen_run IS NOT NULL
        GROUP BY last_seen_run
        ORDER BY MAX(last_seen_at) DESC
        LIMIT 1
        """
    ).fetchone()
    return row[0] if row else None


def build_gold(db_path: Path, run_id: str | None = None) -> None:
    """Create or replace the gold analytics views in the warehouse database.

    ``run_id`` is baked into ``gold.new_this_run``; when omitted, the most
    recent run is used. ``disappeared_this_run`` is staleness-based and does
    not depend on a run id.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS gold")

        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.ranked_jobs AS
            SELECT j.*,
                   c.org_type AS company_type,
                   c.stock_symbol AS company_stock_symbol,
                   CAST(DATEDIFF('day', CAST(j.date_posted AS DATE), CURRENT_DATE)
                        AS INTEGER) AS days_since_posted,
                   {_DAYS_SINCE_SEEN} AS days_since_seen
            FROM silver.jobs j
            LEFT JOIN silver.dim_company c ON j.company_id = c.company_id
            WHERE j.overall_score IS NOT NULL
              AND {_NOT_STALE}
            ORDER BY j.overall_score DESC
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.by_sector AS
            SELECT end_client_sector, COUNT(*) AS job_count
            FROM silver.jobs
            WHERE {_NOT_STALE}
            GROUP BY end_client_sector
            ORDER BY job_count DESC, end_client_sector
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.by_tier AS
            SELECT recommendation_tier, COUNT(*) AS job_count
            FROM silver.jobs
            WHERE {_NOT_STALE}
            GROUP BY recommendation_tier
            ORDER BY job_count DESC, recommendation_tier
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.job_history AS
            SELECT id, source_board, title, company, apply_url,
                   first_seen_run, first_seen_at,
                   last_seen_run, last_seen_at,
                   is_active,
                   CAST(DATEDIFF('day', first_seen_at, last_seen_at) AS INTEGER)
                     AS days_active,
                   {_DAYS_SINCE_SEEN} AS days_since_seen
            FROM silver.jobs
            """
        )
        con.execute(
            """
            CREATE OR REPLACE VIEW gold.weekly_snapshot AS
            WITH weeks AS (
                SELECT DISTINCT CAST(date_trunc('week', first_seen_at) AS DATE) AS week_start
                FROM silver.jobs
                UNION
                SELECT DISTINCT CAST(date_trunc('week', last_seen_at) AS DATE)
                FROM silver.jobs
            )
            SELECT w.week_start, COUNT(*) AS active_jobs
            FROM weeks w
            JOIN silver.jobs j
              ON CAST(j.first_seen_at AS DATE) <= w.week_start + INTERVAL 6 DAY
             AND CAST(j.last_seen_at AS DATE) >= w.week_start
            GROUP BY w.week_start
            ORDER BY w.week_start
            """
        )

        # disappeared_this_run is staleness-based (not run-scoped): a job has
        # "disappeared" when it has not been seen within the staleness horizon.
        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.disappeared_this_run AS
            SELECT * FROM silver.jobs
            WHERE {_STALE}
            """
        )

        run = run_id or latest_run(con)
        if run is None:
            con.execute(
                "CREATE OR REPLACE VIEW gold.new_this_run AS SELECT * FROM silver.jobs WHERE FALSE"
            )
            return

        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.new_this_run AS
            SELECT * FROM silver.jobs WHERE first_seen_run = {sql_literal(run)}
            """
        )
