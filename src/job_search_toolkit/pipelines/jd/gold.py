"""DuckDB gold layer: analytics views over the silver.jobs warehouse table.

Views live in the ``gold`` schema of the warehouse database
(``data/warehouse/jobs.db``) and are ``CREATE OR REPLACE`` on every run:

- ``ranked_jobs``            — active, scored jobs ordered by overall_score DESC
- ``by_sector`` / ``by_tier`` — active job counts grouped by sector / tier
- ``job_history``            — full time-series: first/last seen, is_active, days active
- ``weekly_snapshot``        — active job counts per ISO week (reconstructed from
  the first/last_seen intervals)
- ``new_this_run``           — jobs whose first_seen_run is the given run
- ``disappeared_this_run``   — jobs that became inactive as of the previous run

The two run-scoped views need a run id baked in as a literal (a view cannot
take parameters). ``build_gold`` takes it explicitly; the ``gold_views``
asset passes the current Dagster run id, the ``pipeline gold`` CLI resolves
the most recent run by ``last_seen_at``.

Usage:
    from job_search_toolkit.pipelines.jd.gold import build_gold
    build_gold(Path("data/warehouse/jobs.db"), run_id="run-xyz")
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .silver import sql_literal


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


def _previous_run(con: duckdb.DuckDBPyConnection, run_id: str) -> str | None:
    """Run id that immediately precedes ``run_id`` (by last_seen_at)."""
    row = con.execute(
        f"""
        SELECT last_seen_run FROM silver.jobs
        WHERE last_seen_run IS NOT NULL AND last_seen_run <> {sql_literal(run_id)}
        GROUP BY last_seen_run
        ORDER BY MAX(last_seen_at) DESC
        LIMIT 1
        """
    ).fetchone()
    return row[0] if row else None


def build_gold(db_path: Path, run_id: str | None = None) -> None:
    """Create or replace the gold analytics views in the warehouse database.

    ``run_id`` is baked into ``gold.new_this_run`` and
    ``gold.disappeared_this_run``; when omitted, the most recent run is used.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS gold")

        con.execute(
            """
            CREATE OR REPLACE VIEW gold.ranked_jobs AS
            SELECT *
            FROM silver.jobs
            WHERE is_active AND overall_score IS NOT NULL
            ORDER BY overall_score DESC
            """
        )
        con.execute(
            """
            CREATE OR REPLACE VIEW gold.by_sector AS
            SELECT end_client_sector, COUNT(*) AS job_count
            FROM silver.jobs
            WHERE is_active
            GROUP BY end_client_sector
            ORDER BY job_count DESC, end_client_sector
            """
        )
        con.execute(
            """
            CREATE OR REPLACE VIEW gold.by_tier AS
            SELECT recommendation_tier, COUNT(*) AS job_count
            FROM silver.jobs
            WHERE is_active
            GROUP BY recommendation_tier
            ORDER BY job_count DESC, recommendation_tier
            """
        )
        con.execute(
            """
            CREATE OR REPLACE VIEW gold.job_history AS
            SELECT id, source_board, title, company, apply_url,
                   first_seen_run, first_seen_at,
                   last_seen_run, last_seen_at,
                   is_active,
                   CAST(DATEDIFF('day', first_seen_at, last_seen_at) AS INTEGER)
                     AS days_active
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
             AND (j.is_active OR CAST(j.last_seen_at AS DATE) >= w.week_start)
            GROUP BY w.week_start
            ORDER BY w.week_start
            """
        )

        run = run_id or latest_run(con)
        if run is None:
            con.execute(
                "CREATE OR REPLACE VIEW gold.new_this_run AS SELECT * FROM silver.jobs WHERE FALSE"
            )
            con.execute(
                "CREATE OR REPLACE VIEW gold.disappeared_this_run AS SELECT * FROM silver.jobs WHERE FALSE"
            )
            return

        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.new_this_run AS
            SELECT * FROM silver.jobs WHERE first_seen_run = {sql_literal(run)}
            """
        )
        prev = _previous_run(con, run)
        if prev is not None:
            con.execute(
                f"""
                CREATE OR REPLACE VIEW gold.disappeared_this_run AS
                SELECT * FROM silver.jobs
                WHERE is_active = FALSE AND last_seen_run = {sql_literal(prev)}
                """
            )
        else:
            con.execute(
                "CREATE OR REPLACE VIEW gold.disappeared_this_run AS SELECT * FROM silver.jobs WHERE FALSE"
            )
