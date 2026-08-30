"""DuckDB gold layer: analytics views over the silver.jobs warehouse table.

Views live in the ``gold`` schema of the warehouse database
(``data/warehouse/jobs.db``) and are ``CREATE OR REPLACE`` on every run:

- ``ranked_jobs``            — scored, non-stale jobs ordered by overall_score DESC;
  exposes ``days_since_posted`` / ``days_since_seen`` so shortlisting can apply
  its own freshness judgment on top of the score. Company enrichment comes from
  the golden ``dim_company`` row (one per company, deduped in place): org_type,
  stock, and news sentiment/notes.
- ``by_sector`` / ``by_tier`` — non-stale job counts grouped by sector / tier
- ``job_history``            — full time-series: first/last seen, is_active,
  days active, days since last seen
- ``weekly_snapshot``        — active job counts per ISO week (reconstructed from
  the first/last_seen interval)
- ``new_this_run``           — jobs whose first_seen_run is the given run
- ``market_pulse``           — per-day-per-board operational series: new jobs
  (by ``first_seen_at``) and seen jobs (by ``last_seen_at``). Gauge how much
  the market is moving day over day
- ``active_recent``          — non-stale jobs whose ``date_posted`` is within
  the last 30 days, per board (the "fresh, currently-listable" pool)

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

from .silver import (
    STALE_AFTER_DAYS,
    ensure_bd_tables,
    ensure_outcomes_table,
    sql_literal,
)

# Days since the last (cold) touch after which a contact is due for a
# follow-up in ``gold.contact_cadence`` / ``gold.next_action``.
BD_CADENCE_THRESHOLD = 7

# Integer days since a job was last seen (never NULL — last_seen_at is set on
# every upsert). Used to gate the non-stale views and define "disappeared".
_DAYS_SINCE_SEEN = (
    "CAST(DATEDIFF('day', CAST(last_seen_at AS DATE), CURRENT_DATE) AS INTEGER)"
)
_NOT_STALE = f"{_DAYS_SINCE_SEEN} <= {STALE_AFTER_DAYS}"
_STALE = f"{_DAYS_SINCE_SEEN} > {STALE_AFTER_DAYS}"

# Feature dimensions scored in ``silver.jobs.scores`` (JSON), used by
# ``gold.score_calibration``. A job with fewer than this many *applied*
# outcomes in a band cannot support a trustworthy advance rate.
CALIBRATION_FEATURES = (
    "pay",
    "flexibility",
    "low_responsibility",
    "tech_match",
)
MIN_ADVANCE_COUNT = 5

_CALIBRATION_SQL = f"""
CREATE OR REPLACE VIEW gold.score_calibration AS
WITH features(feature) AS (
    VALUES {", ".join(f"({sql_literal(f)})" for f in CALIBRATION_FEATURES)}
),
bands(band_start, band_end) AS (
    VALUES (0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0)
),
scored AS (
    -- One row per (job, feature): the feature score pulled from the
    -- ``scores`` JSON. NULL feature scores (unscored dimension) drop out.
    SELECT j.id AS job_id,
           f.feature,
           TRY_CAST(json_extract_string(j.scores, '$.' || f.feature) AS DOUBLE)
             AS feature_score
    FROM silver.jobs j
    CROSS JOIN features f
    WHERE j.scores IS NOT NULL
),
applied AS (
    SELECT DISTINCT job_id FROM silver.fact_outcome_event WHERE stage = 'applied'
),
advanced AS (
    -- Reached interview/offer AND had an applied event first.
    SELECT DISTINCT e.job_id
    FROM silver.fact_outcome_event e
    JOIN applied a ON a.job_id = e.job_id
    WHERE e.stage IN ('interview', 'offer')
),
banded AS (
    -- Left half-open bands; the top band is closed so 1.0 lands in 0.75-1.0.
    SELECT s.feature,
           b.band_start,
           b.band_end,
           s.job_id,
           (a.job_id IS NOT NULL) AS did_apply,
           (adv.job_id IS NOT NULL) AS did_advance
    FROM scored s
    CROSS JOIN bands b
    LEFT JOIN applied a ON a.job_id = s.job_id
    LEFT JOIN advanced adv ON adv.job_id = s.job_id
    WHERE s.feature_score >= b.band_start
      AND (s.feature_score < b.band_end
           OR (b.band_end = 1.0 AND s.feature_score <= 1.0))
),
counts AS (
    SELECT feature, band_start, band_end,
           COUNT(job_id) AS jobs_in_band,
           COUNT(*) FILTER (WHERE did_apply) AS applied_count,
           COUNT(*) FILTER (WHERE did_apply AND did_advance) AS advanced_count
    FROM banded
    GROUP BY feature, band_start, band_end
)
SELECT f.feature,
       b.band_start,
       b.band_end,
       COALESCE(c.jobs_in_band, 0) AS jobs_in_band,
       COALESCE(c.applied_count, 0) AS applied_count,
       COALESCE(c.advanced_count, 0) AS advanced_count,
       CASE
           WHEN COALESCE(c.applied_count, 0) < {MIN_ADVANCE_COUNT} THEN NULL
           ELSE COALESCE(c.advanced_count, 0)::DOUBLE
                / COALESCE(c.applied_count, 0)::DOUBLE
       END AS advance_rate,
       CASE
           WHEN COALESCE(c.applied_count, 0) < {MIN_ADVANCE_COUNT}
               THEN 'not enough data'
           ELSE 'ok'
       END AS confidence_note
FROM features f
CROSS JOIN bands b
LEFT JOIN counts c
  ON c.feature = f.feature
 AND c.band_start = b.band_start
 AND c.band_end = b.band_end
ORDER BY f.feature, b.band_start
"""




def build_score_calibration(con: duckdb.DuckDBPyConnection) -> None:
    """Create or replace ``gold.score_calibration`` (run-independent).

    Per-feature advance rates by score band with an honest confidence note
    (``not enough data`` below ``MIN_ADVANCE_COUNT``; ``advance_rate`` NULL
    when there is not enough data — never a fabricated rate). Split out of
    ``build_gold`` so it is testable against a minimal warehouse that only
    needs ``silver.jobs`` + ``silver.fact_outcome_event``.
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    # The view reads silver.fact_outcome_event; ensure it exists so pipeline
    # gold works on a warehouse the outcome sync hasn't run on yet (empty ->
    # "not enough data" bands, never a crash).
    ensure_outcomes_table(con)
    con.execute(_CALIBRATION_SQL)



def _silver_table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """True when ``silver.<table>`` exists in the warehouse."""
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        f"WHERE table_schema = {sql_literal('silver')} "
        f"AND table_name = {sql_literal(table)}"
    ).fetchone()
    return bool(row and row[0])


# Each BD gold view with the silver tables it reads. A view is only created
# when every source table exists, so ``build_bd_views`` no-ops per view on a
# warehouse the BD sync has not populated yet (never a crash).
_BD_VIEWS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("dim_person", "fact_touch"),
        f"""
        CREATE OR REPLACE VIEW gold.contact_cadence AS
        WITH last_touch AS (
            SELECT person_id, MAX(event_date) AS last_touch_date
            FROM silver.fact_touch
            WHERE person_id IS NOT NULL
            GROUP BY person_id
        )
        SELECT p.person_id,
               p.name,
               p.company_id,
               lt.last_touch_date,
               CAST(DATEDIFF('day', lt.last_touch_date, CURRENT_DATE) AS INTEGER)
                 AS days_since_last_touch
        FROM silver.dim_person p
        LEFT JOIN last_touch lt ON lt.person_id = p.person_id
        WHERE lt.last_touch_date IS NULL
           OR DATEDIFF('day', lt.last_touch_date, CURRENT_DATE)
              >= {BD_CADENCE_THRESHOLD}
        """,
    ),
    (
        ("dim_person", "fact_referral"),
        """
        CREATE OR REPLACE VIEW gold.referral_funnel AS
        SELECT r.referral_id,
               r.referrer_person_id,
               rp.name AS referrer_name,
               r.target_person_id,
               tp.name AS target_name,
               r.target_company_id,
               r.status,
               r.event_date
        FROM silver.fact_referral r
        LEFT JOIN silver.dim_person rp ON rp.person_id = r.referrer_person_id
        LEFT JOIN silver.dim_person tp ON tp.person_id = r.target_person_id
        """,
    ),
    (
        ("fact_inbound_attribution",),
        """
        CREATE OR REPLACE VIEW gold.inbound_conversion AS
        SELECT source_asset,
               COUNT(*) AS inbound_count,
               COUNT(DISTINCT person_id) AS unique_contacts
        FROM silver.fact_inbound_attribution
        GROUP BY source_asset
        """,
    ),
    (
        ("fact_touch",),
        """
        CREATE OR REPLACE VIEW gold.event_funnel AS
        SELECT channel,
               status,
               COUNT(*) AS touches,
               MIN(event_date) AS first_touch,
               MAX(event_date) AS last_touch
        FROM silver.fact_touch
        GROUP BY channel, status
        """,
    ),
    (
        ("dim_person", "fact_touch"),
        f"""
        CREATE OR REPLACE VIEW gold.next_action AS
        SELECT p.person_id,
               p.name,
               COALESCE(
                   p.follow_up_due_date,
                   lt.last_touch_date + INTERVAL {BD_CADENCE_THRESHOLD} DAY,
                   CAST(CURRENT_DATE AS DATE)
               ) AS follow_up_by,
               lt.last_touch_date
        FROM silver.dim_person p
        LEFT JOIN (
            SELECT person_id, MAX(event_date) AS last_touch_date
            FROM silver.fact_touch
            WHERE person_id IS NOT NULL
            GROUP BY person_id
        ) lt ON lt.person_id = p.person_id
        """,
    ),
    (
        ("dim_person", "fact_touch", "fact_referral"),
        """
        CREATE OR REPLACE VIEW gold.relationship AS
        SELECT t.person_id,
               p.name,
               'touch' AS fact_type,
               t.touch_id AS fact_id,
               t.event_date
        FROM silver.fact_touch t
        LEFT JOIN silver.dim_person p ON p.person_id = t.person_id
        UNION ALL
        SELECT r.target_person_id,
               tp.name,
               'referral' AS fact_type,
               r.referral_id AS fact_id,
               r.event_date
        FROM silver.fact_referral r
        LEFT JOIN silver.dim_person tp ON tp.person_id = r.target_person_id
        """,
    ),
    (
        ("lead",),
        """
        CREATE OR REPLACE VIEW gold.lead_rank AS
        SELECT person_id,
               company_id,
               intent,
               fit,
               access,
               urgency,
               lead_score
        FROM silver.lead
        ORDER BY lead_score DESC
        """,
    ),
    (
        ("lead",),
        """
        CREATE OR REPLACE VIEW gold.lead_score_calibration AS
        WITH bands(band_start, band_end) AS (
            VALUES (0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0)
        ),
        scored AS (
            SELECT lead_score FROM silver.lead WHERE lead_score IS NOT NULL
        )
        SELECT COUNT(s.lead_score) AS lead_count,
               b.band_start,
               b.band_end
        FROM bands b
        LEFT JOIN scored s
          ON s.lead_score >= b.band_start
         AND (s.lead_score < b.band_end
              OR (b.band_end = 1.0 AND s.lead_score <= 1.0))
        GROUP BY b.band_start, b.band_end
        ORDER BY b.band_start
        """,
    ),
)


def build_bd_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create or replace the BD gold views over the WS7 silver tables.

    Each view is created only when its source silver tables exist, so this is
    safe to call on any warehouse (missing tables -> that view is skipped,
    existing views are untouched). Independent of the jobs gold views.
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    for tables, ddl in _BD_VIEWS:
        if all(_silver_table_exists(con, t) for t in tables):
            con.execute(ddl)

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

        # BD/CRM + lead gold views (WS7 Epic 7.1/7.2): each view no-ops when
        # its source silver table is absent; independent of ranked_jobs.
        ensure_bd_tables(con)
        from .score_engine import ensure_lead_table

        ensure_lead_table(con)
        build_bd_views(con)

        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.ranked_jobs AS
            SELECT j.*,
                   c.org_type AS company_type,
                   c.stock_symbol AS company_stock_symbol,
                   c.news_sentiment AS company_news_sentiment,
                   c.news_notes AS company_news_notes,
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

        # market_pulse: the day-over-day operational series, per board. "new"
        # is a job's first_seen day (reliable — a genuinely new posting always
        # sits at the top of a time-sorted window, so it is always captured);
        # "seen" is a job's last_seen day. NOTE: seen is a lower bound, not the
        # board's true total — every board caps its scrape window
        # (hiringcafe_max_pages, wttj_max_jobs, --max-pages, ...), so a job past
        # the cap is never re-scraped and its last_seen_at does not advance even
        # while it is still live. Read a rising "new" series as real market
        # movement; read "seen" as "what fit in the result window that day".
        con.execute(
            """
            CREATE OR REPLACE VIEW gold.market_pulse AS
            WITH new_by_day AS (
                SELECT source_board,
                       CAST(first_seen_at AS DATE) AS day,
                       COUNT(*) AS new_jobs
                FROM silver.jobs
                WHERE first_seen_at IS NOT NULL
                GROUP BY source_board, CAST(first_seen_at AS DATE)
            ),
            seen_by_day AS (
                SELECT source_board,
                       CAST(last_seen_at AS DATE) AS day,
                       COUNT(*) AS seen_jobs
                FROM silver.jobs
                WHERE last_seen_at IS NOT NULL
                GROUP BY source_board, CAST(last_seen_at AS DATE)
            )
            SELECT COALESCE(n.source_board, s.source_board) AS source_board,
                   COALESCE(n.day, s.day) AS day,
                   COALESCE(n.new_jobs, 0) AS new_jobs,
                   COALESCE(s.seen_jobs, 0) AS seen_jobs
            FROM new_by_day n
            FULL OUTER JOIN seen_by_day s
              ON n.source_board = s.source_board AND n.day = s.day
            ORDER BY day, source_board
            """
        )

        # active_recent: the "fresh, currently-listable" pool — non-stale jobs
        # whose posting date is within the last 30 days, per board. Like all
        # non-stale views this is bounded by what the scrape window re-saw, so
        # a board with a small cap under-counts its truly-live fresh jobs.
        con.execute(
            f"""
            CREATE OR REPLACE VIEW gold.active_recent AS
            SELECT source_board, COUNT(*) AS active_recent_jobs
            FROM silver.jobs
            WHERE CAST(date_posted AS DATE) >= CURRENT_DATE - INTERVAL 30 DAY
              AND {_NOT_STALE}
            GROUP BY source_board
            ORDER BY active_recent_jobs DESC, source_board
            """
        )

        # score_calibration is run-independent: per-feature advance rates
        # by score band, with an honest confidence note (no fabricated power).
        build_score_calibration(con)

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
