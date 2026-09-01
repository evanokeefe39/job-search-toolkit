"""Silver layer: DuckDB warehouse schema, upsert, and enrichment helpers.

The warehouse is a single DuckDB file (``data/warehouse/jobs.db``) with two
schemas:

- ``silver.jobs`` — the fact table: one row per unique ``(id, source_board)``
  (a job posted on both boards gets two rows — cross-board dedup is out of
  scope). Lineage columns track ``first_seen_*``/``last_seen_*``,
  ``is_active``, ``enriched_at`` and ``enrichment_version``. Jobs are never
  deleted; listings that disappear from the boards are marked inactive.
- ``silver.dim_board`` / ``silver.dim_company`` / ``silver.dim_date`` —
  pragmatic Kimball dims: board is static, company is keyed per (normalized
  name, source_board), date is a spine over ``date_posted``. Company research
  (LLM) lives on ``dim_company`` and never blocks the ranking path.
- ``gold.*`` — analytics views (see gold.py).

Enrichment state lives in column nullability, not in a ``_enrichment`` dict:
``description_language = 'fr'`` means "needs translation", an empty
``technologies`` array means "needs tech extraction", etc. A row is fully
enriched when all stage gates pass, at which point ``enriched_at`` is set.

DuckDB note: bound parameters (``?`` placeholders) deadlock in duckdb 1.5.5
on Windows/Python 3.14 (verified empirically), so all values are rendered
into SQL via ``sql_literal``/``sql_json``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import duckdb

from . import db
from .config import WAREHOUSE_DB, get_enrichment_version

def connect() -> duckdb.DuckDBPyConnection:
    """Open the warehouse database (creating the file if missing)."""
    return db.connect()

# Pipeline-internal keys that never become warehouse columns.
_SKIP_KEYS = {"_enrichment", "_source", "company_info"}

# Lineage columns appended to every row (see plan: medallion-data-warehouse).
LINEAGE_COLUMNS: list[tuple[str, str]] = [
    ("first_seen_run", "VARCHAR"),
    ("first_seen_at", "TIMESTAMP"),
    ("last_seen_run", "VARCHAR"),
    ("last_seen_at", "TIMESTAMP"),
    ("is_active", "BOOLEAN"),
    ("enriched_at", "TIMESTAMP"),
    ("enrichment_version", "INTEGER"),
    ("created_at", "TIMESTAMP"),
    ("updated_at", "TIMESTAMP"),
]
_LINEAGE_KEYS = {k for k, _t in LINEAGE_COLUMNS}

# --- Kimball dimensions (pragmatic 3+1) -------------------------------------
# dim_board is static; dim_company is keyed per (normalized name, source_board)
# and carries the fields that used to live in the fact table's company_info
# JSON column; dim_date is a spine over the fact table's date_posted.

BOARD_DIMENSIONS: dict[str, tuple[str, str, str]] = {
    "freework": ("Free-Work", "fr", "https://www.free-work.com"),
    "hiringcafe": ("HiringCafe", "en", "https://www.hiringcafe.com"),
    "hellowork": ("HelloWork", "fr", "https://www.hellowork.com"),
    "englishjobs": ("EnglishJobs", "en", "https://www.englishjobs.com"),
    "faruse": ("Faruse", "en", "https://www.faruse.com"),
    "wwr": ("We Work Remotely", "en", "https://weworkremotely.com"),
    "remoteok": ("RemoteOK", "en", "https://remoteok.com"),
    "datasciencejobs": ("DataScienceJobs", "en", "https://datasciencejobs.com"),
    "linkedin_jobs": ("LinkedIn Jobs", "en", "https://www.linkedin.com/jobs/"),
    "linkedin_posts": ("LinkedIn Posts", "en", "https://www.linkedin.com/posts/"),
    "wttj": ("Welcome to the Jungle", "fr", "https://www.welcometothejungle.com"),
    "builtin": ("Built In", "en", "https://builtin.com/jobs/eu/france"),
}

# dim_company columns mirroring the canonical CompanyInfo dict (schemas.py):
# same field names so fetch_jobs(join_company=True) can rebuild company_info.
DIM_COMPANY_COLUMNS: list[tuple[str, str]] = [
    ("company_id", "VARCHAR"),
    ("name", "VARCHAR"),          # normalized: lowercase, collapsed whitespace
    ("display_name", "VARCHAR"),  # as written on the board
    ("source_board", "VARCHAR"),
    ("industry", "JSON"),
    ("size_employees", "BIGINT"),
    ("year_founded", "BIGINT"),
    ("hq_country", "VARCHAR"),
    ("org_type", "VARCHAR"),
    ("company_type", "VARCHAR"),  # derived growth-stage proxy (company_type_derived asset)
    ("stock_symbol", "VARCHAR"),
    ("stock_exchange", "VARCHAR"),
    ("latest_funding_type", "VARCHAR"),
    ("latest_funding_amount_usd", "BIGINT"),
    ("homepage_url", "VARCHAR"),
    ("enriched_at", "TIMESTAMP"),
    ("enrichment_version", "INTEGER"),
    ("news_notes", "JSON"),
    ("news_sentiment", "VARCHAR"),
    ("news_checked_at", "TIMESTAMP"),
    ("insee_employee_range", "VARCHAR"),
    ("insee_legal_type", "VARCHAR"),
    ("insee_checked_at", "TIMESTAMP"),
    ("dedup_version", "VARCHAR"),  # golden-record derivation marker (company_resolve)
    ("company_sources", "JSON"),   # provenance refs from the CSV enrichment source (company_enrichment_ingested)
]

# --- Enrichment stage gates -------------------------------------------------
# Each gate selects rows the stage still has to process. Column nullability /
# emptiness is the source of truth — there is no _enrichment flag anymore.

# Staleness horizon: a job whose last_seen_at is older than this many days is
# treated as likely filled/expired. Jobs are never deactivated — staleness is
# inferred from time since last seen (see gold.py, score_engine.py).
STALE_AFTER_DAYS = 60

GATE_TRANSLATE = (
    "is_active AND description_language = 'fr' AND TRIM(description_text) <> ''"
)
# Gates are NULL-based: a written result (even an empty list or 'unknown') is
# terminal — the LLM was asked and answered, so the row is never re-selected.
# 'unknown' means "researched, nothing found"; NULL means "not yet processed".
# The freework adapter emits NULL for absent source data (see adapt_freework).
GATE_TECH = "is_active AND technologies IS NULL"
GATE_CLASSIFY = (
    "is_active AND source_board NOT IN ('hiringcafe', 'linkedin_posts') "
    "AND engagement_type IS NULL"
)
GATE_POST_ENRICH = (
    "is_active AND source_board = 'linkedin_posts' "
    "AND (title = '' OR location_raw = '')"
)
# Poster-location enrichment: rows with a known poster profile URL whose
# location is not yet scraped. NULL/empty poster_url rows are never selected.
GATE_POSTER = (
    "is_active AND source_board = 'linkedin_posts' "
    "AND poster_url <> '' AND poster_location IS NULL"
)
GATE_SCORE = "is_active AND overall_score IS NULL"

# Company research is dimension-scoped: golden grain (one dim_company row per
# real company), so the gate is simply the missing-enrichment condition.
# hiringcafe rows ship org_type from the source and are non-NULL already;
# after the golden-record dedup no per-board filter may hide an otherwise-
# unenriched company from research.
GOLDEN_DIM_COMPANY_GATE = "company_type IS NULL"

# Complement of the gates — true when the stage's output is present.
DONE_TRANSLATE = "(description_language <> 'fr' OR TRIM(description_text) = '')"
DONE_TECH = "technologies IS NOT NULL"
DONE_CLASSIFY = (
    "source_board = 'hiringcafe' OR engagement_type IS NOT NULL"
)
# Company research no longer gates row-level enrichment (it lives on
# dim_company), so DONE_ALL covers only the per-row LLM stages.
DONE_ALL = (
    f"({DONE_TRANSLATE}) AND ({DONE_TECH}) AND ({DONE_CLASSIFY})"
)


# ---------------------------------------------------------------------------
# SQL value rendering (no bound parameters — see module docstring)
# ---------------------------------------------------------------------------

def sql_literal(value: Any) -> str:
    """Render a Python scalar as a DuckDB SQL literal.

    Strings are single-quoted with embedded quotes doubled — safe for
    arbitrary text.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def sql_json(value: Any) -> str:
    """Render a nested value as a DuckDB JSON literal (NULL stays NULL)."""
    if value is None:
        return "NULL"
    return "'" + json.dumps(value, ensure_ascii=False, default=str).replace("'", "''") + "'::JSON"


def _infer_column_type(values: list[Any]) -> str:
    """DuckDB type for a column, from the values observed across all rows."""
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "VARCHAR"
    if all(isinstance(v, bool) for v in non_null):
        return "BOOLEAN"
    if all(isinstance(v, int) for v in non_null):
        return "BIGINT"
    if all(isinstance(v, (int, float)) for v in non_null):
        return "DOUBLE"
    if all(isinstance(v, (dict, list)) for v in non_null):
        return "JSON"
    return "VARCHAR"


# ---------------------------------------------------------------------------
# Schema + upsert
# ---------------------------------------------------------------------------

def normalize_company_name(name: str) -> str:
    """Canonical dim_company key: lowercase, whitespace collapsed."""
    return " ".join(str(name or "").lower().split())


def company_id(name: str, source_board: str) -> str:
    """Stable surrogate key: SHA-1 of (normalized name, source_board)."""
    norm = normalize_company_name(name)
    digest = hashlib.sha1(f"{source_board}|{norm}".encode("utf-8")).hexdigest()
    return digest[:16]


def _company_dim_row(cid: str, name: str, board: str, ci: dict) -> dict:
    """One dim_company row from a company_info dict + the source board."""
    return {
        "company_id": cid,
        "name": normalize_company_name(name),
        "display_name": name,
        "source_board": board,
        "industry": ci.get("industry") or [],
        "size_employees": ci.get("size_employees"),
        "year_founded": ci.get("year_founded"),
        "hq_country": ci.get("hq_country"),
        "org_type": ci.get("org_type"),
        "stock_symbol": ci.get("stock_symbol"),
        "stock_exchange": ci.get("stock_exchange"),
        "latest_funding_type": ci.get("latest_funding_type"),
        "latest_funding_amount_usd": ci.get("latest_funding_amount_usd"),
        "homepage_url": ci.get("homepage_url"),
        "company_sources": ci.get("company_sources"),
        "enrichment_version": get_enrichment_version(),
    }


def _job_company_row(job: dict) -> dict | None:
    """dim_company row for a job, or None when the job has no company name."""
    ci = job.get("company_info") or {}
    name = ci.get("name") or job.get("company") or ""
    if not name:
        return None
    return _company_dim_row(company_id(name, job["source_board"]), name, job["source_board"], ci)


def ensure_dims(con: duckdb.DuckDBPyConnection) -> None:
    """Create the Kimball dims (board/company/date) if missing.

    Idempotent. ``dim_board`` is seeded from ``BOARD_DIMENSIONS`` (static);
    ``dim_company`` and ``dim_date`` are populated from the fact table by
    ``upsert_run`` / ``refresh_dim_date``. A one-time migration splits the
    legacy ``company_info`` JSON column into ``dim_company`` (no-op once the
    column is gone).
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS silver.dim_board (
            board_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            description_language VARCHAR,
            base_url VARCHAR
        )
        """
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS silver.dim_company ("
        + ", ".join(f'"{k}" {t}' for k, t in DIM_COMPANY_COLUMNS)
        + ', PRIMARY KEY ("company_id"))'
    )
    # Idempotent migration: ALTER ADD COLUMN for dim_company fields added
    # after the table's first creation (e.g. news_notes / insee_*). CREATE
    # TABLE IF NOT EXISTS won't add columns to an existing table.
    existing = {r[1] for r in con.execute("PRAGMA table_info('silver.dim_company')").fetchall()}
    for name, typ in DIM_COMPANY_COLUMNS:
        if name not in existing:
            con.execute(f'ALTER TABLE silver.dim_company ADD COLUMN "{name}" {typ}')
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS silver.dim_date (
            date_id VARCHAR PRIMARY KEY,
            iso_week INTEGER,
            month INTEGER,
            quarter INTEGER,
            year INTEGER
        )
        """
    )
    for bid, (name, lang, url) in BOARD_DIMENSIONS.items():
        con.execute(
            f"INSERT INTO silver.dim_board VALUES "
            f"({sql_literal(bid)}, {sql_literal(name)}, {sql_literal(lang)}, {sql_literal(url)}) "
            f"ON CONFLICT (board_id) DO NOTHING"
        )
    # One-time migration for pre-Kimball warehouses (no-op once company_info
    # Golden-record registry (company golden-record dedup): every board-side
    # name maps to its golden dim_company row. Idempotent, additive-only.
    from .company_resolve import ensure_company_alias

    ensure_company_alias(con)
    # is gone): backfill dim_company + company_id, then drop the JSON column.
    _migrate_company_info(con)


def _upsert_dim_companies(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    """Upsert dim_company rows on company_id (source data wins per run).
    New rows insert; existing rows keep LLM-researched fields (``org_type``,
    ``hq_country``, ``stock_*``, funding) unless the source provides a
    non-NULL value — scraper-parsed fields refresh, research output survives.
    ``company_type``/``company_sources`` follow the same COALESCE rule: a
    trusted CSV value wins, a NULL leaves the stored/derived value in place.
    """
    if not rows:
        return
    fields = [
        "name", "display_name", "source_board", "industry", "size_employees",
        "year_founded", "hq_country", "org_type", "company_type",
        "company_sources", "stock_symbol",
        "stock_exchange", "latest_funding_type", "latest_funding_amount_usd",
        "homepage_url", "enrichment_version",
    ]
    json_fields = {"industry", "company_sources"}
    rows_sql: list[str] = []
    for row in rows:
        values = [sql_literal(row.get("company_id"))]
        for f in fields:
            v = row.get(f)
            values.append(sql_json(v) if f in json_fields else sql_literal(v))
        rows_sql.append("(" + ", ".join(values) + ")")

    conflict_set = ", ".join(
        f'"{f}" = COALESCE(EXCLUDED."{f}", "{f}")'
        for f in fields
        if f not in ("source_board", "enrichment_version")
    )
    con.execute(
        f"""
        INSERT INTO silver.dim_company (company_id, {", ".join(f'"{f}"' for f in fields)})
        VALUES {", ".join(rows_sql)}
        ON CONFLICT ("company_id") DO UPDATE SET {conflict_set}
        """
    )


def refresh_dim_date(con: duckdb.DuckDBPyConnection) -> None:
    """Populate ``dim_date`` from the fact table's ``date_posted`` values.

    Missing dates produce no row (NULL ``date_id`` on the fact side, never a
    fake date). Idempotent via ``ON CONFLICT DO NOTHING``.
    """
    con.execute(
        """
        INSERT INTO silver.dim_date (date_id, iso_week, month, quarter, year)
        SELECT DISTINCT
            CAST(date_posted AS DATE)::VARCHAR,
            CAST(date_part('week', CAST(date_posted AS DATE)) AS INTEGER),
            CAST(date_part('month', CAST(date_posted AS DATE)) AS INTEGER),
            CAST(date_part('quarter', CAST(date_posted AS DATE)) AS INTEGER),
            CAST(date_part('year', CAST(date_posted AS DATE)) AS INTEGER)
        FROM silver.jobs
        WHERE date_posted IS NOT NULL
          AND TRY_CAST(date_posted AS DATE) IS NOT NULL
        ON CONFLICT (date_id) DO NOTHING
        """
    )


def _merge_company_ci(entries: list[tuple[str | None, dict]]) -> dict:
    """Merge per-job company_info snapshots into one canonical dict.

    Entries are ``(last_seen_at, company_info)`` pairs. The result is a pure
    function of the row set: sorted most-recent-first (NULL ``last_seen_at``
    treated as oldest), then each field takes the first non-NULL value in
    that order. Deterministic and lossless — a value present in ANY row of
    the company survives, and newer research wins ties. This is the seed rule
    for ``dim_company`` (per-company canonicalization; legacy per-row
    research was inconsistent across re-runs).
    """
    ordered = sorted(entries, key=lambda e: e[0] or "", reverse=True)
    merged: dict = {}
    for _seen_at, ci in ordered:
        for k, v in ci.items():
            if v is not None and k not in merged:
                merged[k] = v
    return merged

def _migrate_company_info(con: duckdb.DuckDBPyConnection) -> None:
    """One-time migration: split legacy ``company_info`` JSON into dim_company.

    Runs only when ``silver.jobs`` still has the pre-Kimball ``company_info``
    column (checked via PRAGMA, not a flag). Backfills ``dim_company`` from
    the stored JSON, sets ``company_id`` on every fact row, then drops the
    column. Idempotent — a no-op once the column is gone or before the fact
    table exists.
    """
    table_exists = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'silver' AND table_name = 'jobs'"
    ).fetchone()[0]
    if not table_exists:
        return
    cols = {r[1] for r in con.execute("PRAGMA table_info('silver.jobs')").fetchall()}
    if "company_info" not in cols:
        return
    # ensure_dims runs before ensure_jobs_table, so the FK column may not
    # exist yet — create it before backfilling company_id on fact rows.
    if "company_id" not in cols:
        con.execute('ALTER TABLE silver.jobs ADD COLUMN "company_id" VARCHAR')
    rows = con.execute(
        "SELECT id, source_board, company, company_info, last_seen_at "
        "FROM silver.jobs"
    ).fetchall()
    per_company: dict[str, list[tuple[str | None, str, str, dict]]] = {}
    for jid, board, company, ci_raw, last_seen in rows:
        ci = json.loads(ci_raw) if isinstance(ci_raw, str) else (ci_raw or {})
        name = ci.get("name") or company or ""
        if not name:
            continue
        cid = company_id(name, board)
        per_company.setdefault(cid, []).append((last_seen, board, company, ci))
        con.execute(
            f"UPDATE silver.jobs SET company_id = {sql_literal(cid)} "
            f"WHERE id = {sql_literal(jid)} AND source_board = {sql_literal(board)}"
        )
    dim_rows: dict[str, dict] = {}
    for cid, entries in per_company.items():
        entries.sort(key=lambda e: e[0] or "", reverse=True)
        merged = _merge_company_ci([(e[0], e[3]) for e in entries])
        _seen_at, board, company, newest_ci = entries[0]
        name = merged.get("name") or newest_ci.get("name") or company or ""
        dim_rows[cid] = _company_dim_row(cid, name, board, merged)
    _upsert_dim_companies(con, list(dim_rows.values()))
    con.execute("ALTER TABLE silver.jobs DROP COLUMN company_info")


def ensure_jobs_table(con: duckdb.DuckDBPyConnection, jobs: list[dict]) -> list[str]:
    """Create ``silver.jobs`` if missing; ALTER ADD COLUMN for new fields.

    Returns the ordered column list: union of top-level keys across ``jobs``
    (minus ``_enrichment``/``_source``/``company_info``) plus the lineage
    columns and the Kimball ``company_id`` FK (VARCHAR, always present).
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")

    keys: list[str] = []
    seen: set[str] = set()
    for job in jobs:
        for key in job:
            if key in _SKIP_KEYS or key in seen:
                continue
            seen.add(key)
            keys.append(key)
    col_types = {k: _infer_column_type([j.get(k) for j in jobs]) for k in keys}

    table_exists = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'silver' AND table_name = 'jobs'"
    ).fetchone()[0]
    existing = (
        {r[1] for r in con.execute("PRAGMA table_info('silver.jobs')").fetchall()}
        if table_exists
        else set()
    )
    if not existing:
        ddl = ", ".join(
            [f'"{k}" {col_types[k]}' for k in keys]
            + ['"company_id" VARCHAR']
            + [f'"{k}" {t}' for k, t in LINEAGE_COLUMNS]
        )
        con.execute(
            f'CREATE TABLE silver.jobs ({ddl}, PRIMARY KEY ("id", "source_board"))'
        )
    else:
        for key in keys + ["company_id"]:
            if key not in existing:
                con.execute(
                    f'ALTER TABLE silver.jobs ADD COLUMN "{key}" '
                    f'{col_types.get(key, "VARCHAR")}'
                )
    return keys + ["company_id"] + [k for k, _t in LINEAGE_COLUMNS]


def upsert_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    jobs: list[dict],
    columns: list[str],
    full_replace: bool = False,
) -> None:
    """Upsert one scrape run's jobs into ``silver.jobs``.

    New rows: full insert with ``first_seen = last_seen = run_id``,
    ``is_active = true``, ``enriched_at = NULL`` (pending enrichment) and the
    current ``enrichment_version``. Existing rows: only ``last_seen*``,
    ``is_active`` and ``updated_at`` change — enrichment columns are preserved
    (job descriptions are assumed immutable between scrapes).

    Company data is written to ``dim_company`` (one row per normalized name +
    source_board); the fact row carries only the ``company_id`` FK. The
    ``company_info`` key on incoming jobs is consumed here, never stored.

    With ``full_replace`` (migration only), the source record's content
    replaces every column except ``id``, ``source_board``, ``first_seen*``
    and ``created_at`` — used when the incoming record is a superset of the
    stored one (e.g. backfilled CSV shadows upgraded with the full JSON).
    """
    dim_rows = [r for r in (_job_company_row(j) for j in jobs) if r is not None]
    _upsert_dim_companies(con, dim_rows)

    insert_cols = ", ".join(f'"{k}"' for k in columns)
    rows_sql: list[str] = []
    for job in jobs:
        values: list[str] = []
        for key in columns:
            if key == "company_id":
                ci = job.get("company_info") or {}
                name = ci.get("name") or job.get("company") or ""
                values.append(sql_literal(company_id(name, job["source_board"])))
            elif key in ("first_seen_run", "last_seen_run"):
                values.append(sql_literal(run_id))
            elif key in ("first_seen_at", "last_seen_at", "created_at", "updated_at"):
                values.append("NOW()")
            elif key == "is_active":
                values.append("TRUE")
            elif key == "enriched_at":
                values.append("NULL")
            elif key == "enrichment_version":
                values.append(str(get_enrichment_version()))
            else:
                value = job.get(key)
                if value is None:
                    values.append("NULL")
                elif isinstance(value, (dict, list)):
                    values.append(sql_json(value))
                else:
                    values.append(sql_literal(value))
        rows_sql.append("(" + ", ".join(values) + ")")

    if not rows_sql:
        return

    if full_replace:
        conflict_set = ", ".join(
            f'"{k}" = EXCLUDED."{k}"'
            for k in columns
            if k not in ("id", "source_board", "first_seen_run", "first_seen_at", "created_at")
        )
    else:
        conflict_set = (
            '"last_seen_run" = EXCLUDED."last_seen_run", '
            '"last_seen_at" = EXCLUDED."last_seen_at", '
            '"is_active" = TRUE, '
            '"updated_at" = NOW()'
        )
    con.execute(
        f"""
        INSERT INTO silver.jobs ({insert_cols})
        VALUES {", ".join(rows_sql)}
        ON CONFLICT ("id", "source_board") DO UPDATE SET
            {conflict_set}
        """
    )


def mark_enriched(con: duckdb.DuckDBPyConnection) -> None:
    """Set ``enriched_at`` on rows where every stage gate now passes.

    ``overall_score`` is cleared so the score stage re-runs on freshly
    completed rows (their inputs changed — e.g. company research landed).
    """
    con.execute(
        f"""
        UPDATE silver.jobs SET
            enriched_at = NOW(),
            overall_score = NULL,
            enrichment_version = {get_enrichment_version()}
        WHERE enriched_at IS NULL AND {DONE_ALL}
        """
    )


def reset_stale(con: duckdb.DuckDBPyConnection, stage: str) -> None:
    """Force re-enrichment of rows at an older ``enrichment_version``.

    Called at the start of each enrichment asset: rows whose version predates
    the current ``ENRICHMENT_VERSION`` have their stage outputs cleared so the
    stage gate re-selects them. Classify and company resets skip hiringcafe
    rows — hiringcafe classify/company data comes from the source, not the
    LLM, and would otherwise be wiped with nothing to restore it.
    """
    stale = f"enrichment_version < {get_enrichment_version()}"
    if stage == "translate":
        con.execute(
            f"UPDATE silver.jobs SET description_language = 'fr' "
            f"WHERE {stale} AND description_language = 'en' AND TRIM(description_text) <> ''"
        )
    elif stage == "tech":
        con.execute(
            f"UPDATE silver.jobs SET technologies = NULL, competencies = NULL, "
            f"seniority_level = NULL, role_category = NULL WHERE {stale}"
        )
    elif stage == "classify":
        con.execute(
            f"UPDATE silver.jobs SET posting_company_type = NULL, end_client_name = NULL, "
            f"end_client_sector = NULL, engagement_type = NULL "
            f"WHERE {stale} AND source_board <> 'hiringcafe'"
        )
    elif stage == "company":
        # Company research is dimension-scoped: reset dim_company rows so the
        # dim gate re-selects them (per company, not per job).
        con.execute(
            f"UPDATE silver.dim_company SET org_type = NULL, company_type = NULL, "
            f"enriched_at = NULL "
            f"WHERE {stale} AND source_board <> 'hiringcafe'"
        )
    elif stage == "company_news":
        # News/INSEE enrichment is dimension-scoped and off the rank; reset
        # the news columns on stale rows so the ``news_checked_at IS NULL``
        # gate re-selects them at the next version.
        con.execute(
            f"UPDATE silver.dim_company SET news_notes = NULL, "
            f"news_sentiment = NULL, news_checked_at = NULL, "
            f"insee_employee_range = NULL, insee_legal_type = NULL, "
            f"insee_checked_at = NULL "
            f"WHERE {stale}"
        )
    elif stage == "score":
        con.execute(f"UPDATE silver.jobs SET overall_score = NULL WHERE {stale}")


# ---------------------------------------------------------------------------
# Row fetching (JSON columns decoded to Python values)
# ---------------------------------------------------------------------------

def _json_columns(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Column names typed JSON in the silver.jobs table."""
    rows = con.execute("PRAGMA table_info('silver.jobs')").fetchall()
    return {r[1] for r in rows if r[2].upper() == "JSON"}


def fetch_jobs(
    con: duckdb.DuckDBPyConnection,
    columns: list[str],
    where: str,
    order: str | None = None,
    join_company: bool = False,
) -> list[dict]:
    """Fetch rows as dicts with JSON columns decoded into Python values.

    With ``join_company=True``, each job dict gains a ``company_info`` dict
    rebuilt from the golden ``dim_company`` row (the same shape the fact table
    used to store as JSON) — after the golden-record dedup there is exactly one
    dim row per real company, so every job of a company (any board) gets the
    same enrichment. Callers keep reading ``job["company_info"]`` unchanged.
    Rows whose company_id has no dim row get ``company_info = {}``.
    """
    json_cols = _json_columns(con)
    sql = "SELECT " + ", ".join(f'"{c}"' for c in columns) + f" FROM silver.jobs WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    out: list[dict] = []
    for row in con.execute(sql).fetchall():
        d = {}
        for col, val in zip(columns, row):
            if col in json_cols and isinstance(val, str):
                d[col] = json.loads(val)
            else:
                d[col] = val
        out.append(d)

    if join_company:
        dims = con.execute(
            "SELECT company_id, name, display_name, industry, size_employees, "
            "year_founded, hq_country, org_type, company_type, stock_symbol, "
            "stock_exchange, "
            "latest_funding_type, latest_funding_amount_usd, homepage_url, "
            "company_sources "
            "FROM silver.dim_company"
        ).fetchall()
        by_id = {r[0]: r for r in dims}
        for job in out:
            dim = by_id.get(job.get("company_id"))
            if dim is None:
                job["company_info"] = {}
                continue
            job["company_info"] = {
                "name": dim[2],  # golden display name (surviving board row)
                "industry": json.loads(dim[3]) if isinstance(dim[3], str) else dim[3],
                "size_employees": dim[4],
                "year_founded": dim[5],
                "hq_country": dim[6],
                "org_type": dim[7],
                "company_type": dim[8],
                "stock_symbol": dim[9],
                "stock_exchange": dim[10],
                "latest_funding_type": dim[11],
                "latest_funding_amount_usd": dim[12],
                "homepage_url": dim[13],
                "company_sources": json.loads(dim[14]) if isinstance(dim[14], str) else dim[14],
            }
    return out


# --- Outcome events (WS1 Epic 1.2) ------------------------------------------

OUTCOME_EVENT_COLUMNS: list[tuple[str, str]] = [
    ("outcome_event_id", "VARCHAR"),
    ("job_id", "VARCHAR"),
    ("stage", "VARCHAR"),
    ("ts", "VARCHAR"),
    ("note", "VARCHAR"),
    ("provenance", "VARCHAR"),
    ("recorded_at", "VARCHAR"),
    ("synced_at", "TIMESTAMP"),
]


def outcome_event_id(job_id: str, stage: str, ts: str, note: str | None,
                     provenance: str) -> str:
    """SHA-1 surrogate key over the event identity (repo convention)."""
    payload = "|".join((str(job_id), str(stage), str(ts),
                        str(note or ""), str(provenance)))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def ensure_outcomes_table(con: duckdb.DuckDBPyConnection) -> None:
    """Create ``silver.fact_outcome_event`` if missing (idempotent).

    ``job_id`` is deliberately NOT a foreign key: an outcome may be recorded
    for an application folder that has no warehouse job yet (nullable join).
    The UNIQUE index mirrors the tracker's own idempotency so re-syncing the
    same events never duplicates rows (NULL note handled via COALESCE).
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    cols = ", ".join(f"{name} {typ}" for name, typ in OUTCOME_EVENT_COLUMNS)
    con.execute(
        f"CREATE TABLE IF NOT EXISTS silver.fact_outcome_event ({cols})"
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_outcome_event_payload "
        "ON silver.fact_outcome_event(job_id, stage, ts, "
        "COALESCE(note, ''), provenance)"
    )




# --- BD/CRM dimensions (WS7 Epic 7.1) ----------------------------------------
# dim_person + the append-only BD fact tables (touch / referral / inbound
# attribution). Same conventions as the jobs warehouse: SHA-1 surrogate keys
# [:16], values rendered via sql_literal (never bound ? params — duckdb 1.5.5
# deadlock on Windows), CREATE IF NOT EXISTS, deterministic idempotent writes.
# Never hardcode a CRM here: callers go through these helpers.

BD_TOUCH_STATUS = ("drafted", "sent", "replied", "meeting", "closed")
BD_DIRECTIONS = ("out", "in")

# Legacy outreach CSV status -> canonical touch status (backfill mapping).
BD_CSV_STATUS_MAP = {
    "found": "drafted",
    "draft_approved": "drafted",
    "sent": "sent",
    "replied": "replied",
    "connected": "replied",
    "no_response": "closed",
    "declined": "closed",
}

DIM_PERSON_COLUMNS: list[tuple[str, str]] = [
    ("person_id", "VARCHAR"),
    ("natural_key", "VARCHAR"),
    ("name", "VARCHAR"),
    ("linkedin_url", "VARCHAR"),
    ("title", "VARCHAR"),
    ("contact_type", "VARCHAR"),
    ("agency", "VARCHAR"),
    ("company_id", "VARCHAR"),
    ("key_source", "VARCHAR"),
    ("follow_up_due_date", "DATE"),
    ("created_at", "TIMESTAMP"),
    ("updated_at", "TIMESTAMP"),
]

FACT_TOUCH_COLUMNS: list[tuple[str, str]] = [
    ("touch_id", "VARCHAR"),
    ("person_id", "VARCHAR"),
    ("company_id", "VARCHAR"),
    ("direction", "VARCHAR"),
    ("channel", "VARCHAR"),
    ("playbook", "VARCHAR"),
    ("status", "VARCHAR"),
    ("event_date", "DATE"),
    ("touch_number", "INTEGER"),
    ("note", "VARCHAR"),
    ("provenance", "VARCHAR"),
    ("recorded_at", "TIMESTAMP"),
]

FACT_REFERRAL_COLUMNS: list[tuple[str, str]] = [
    ("referral_id", "VARCHAR"),
    ("referrer_person_id", "VARCHAR"),
    ("target_person_id", "VARCHAR"),
    ("target_company_id", "VARCHAR"),
    ("status", "VARCHAR"),
    ("event_date", "DATE"),
    ("note", "VARCHAR"),
    ("provenance", "VARCHAR"),
    ("recorded_at", "TIMESTAMP"),
]

FACT_INBOUND_COLUMNS: list[tuple[str, str]] = [
    ("attribution_id", "VARCHAR"),
    ("person_id", "VARCHAR"),
    ("company_id", "VARCHAR"),
    ("source_asset", "VARCHAR"),
    ("event_date", "DATE"),
    ("note", "VARCHAR"),
    ("provenance", "VARCHAR"),
    ("recorded_at", "TIMESTAMP"),
]


def person_id(natural_key: str) -> str:
    """SHA-1 surrogate key over a dim_person natural key (repo convention)."""
    return hashlib.sha1(str(natural_key).encode("utf-8")).hexdigest()[:16]


def _fact_id(*parts: Any) -> str:
    """SHA-1 surrogate key over the "|" -joined fact identity (None -> '')."""
    payload = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _fact_exists(con: duckdb.DuckDBPyConnection, table: str, id_col: str,
                 fid: str) -> bool:
    """True when a fact row with this deterministic id is already stored."""
    row = con.execute(
        f"SELECT count(*) FROM silver.{table} WHERE {id_col} = {sql_literal(fid)}"
    ).fetchone()
    return bool(row and row[0])


def ensure_bd_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create the BD schema objects if missing (idempotent).

    Primary keys on the deterministic surrogate ids carry idempotency —
    re-inserting the same event hits ON CONFLICT and is a no-op. IDs are
    deliberately not foreign keys: facts may reference people/companies
    that have no dimension row yet (nullable join).
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    con.execute(
        "CREATE TABLE IF NOT EXISTS silver.dim_person ("
        + ", ".join(f"{name} {typ}" for name, typ in DIM_PERSON_COLUMNS)
        + ", PRIMARY KEY (person_id))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS silver.fact_touch ("
        + ", ".join(f"{name} {typ}" for name, typ in FACT_TOUCH_COLUMNS)
        + ", PRIMARY KEY (touch_id))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS silver.fact_referral ("
        + ", ".join(f"{name} {typ}" for name, typ in FACT_REFERRAL_COLUMNS)
        + ", PRIMARY KEY (referral_id))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS silver.fact_inbound_attribution ("
        + ", ".join(f"{name} {typ}" for name, typ in FACT_INBOUND_COLUMNS)
        + ", PRIMARY KEY (attribution_id))"
    )


def _person_natural_key(person: dict) -> tuple[str, str]:
    """Natural key + key_source: normalized LinkedIn URL when present, else
    "normalized name|company_id" (ambiguous name-keyed merges stay visible
    via key_source='name')."""
    url = str(person.get("linkedin_url") or "").strip().lower()
    if url:
        return url, "linkedin"
    name = normalize_company_name(person.get("name") or "")
    return f"{name}|{person.get('company_id') or ''}", "name"


def upsert_person(con: duckdb.DuckDBPyConnection, person: dict) -> str:
    """Upsert one ``silver.dim_person`` row; returns its person_id.

    Source fields win on conflict but ``created_at`` is preserved (first
    sighting is the durable fact). ``follow_up_due_date`` is the human-set
    due date consumed by gold.next_action.
    """
    natural_key, key_source = _person_natural_key(person)
    pid = person_id(natural_key)
    values = {
        "natural_key": natural_key,
        "name": person.get("name"),
        "linkedin_url": person.get("linkedin_url"),
        "title": person.get("title"),
        "contact_type": person.get("contact_type"),
        "agency": person.get("agency"),
        "company_id": person.get("company_id"),
        "key_source": key_source,
        "follow_up_due_date": person.get("follow_up_due_date"),
    }
    cols = list(values)
    con.execute(
        f"""
        INSERT INTO silver.dim_person (
            person_id, {", ".join(cols)}, created_at, updated_at
        ) VALUES (
            {sql_literal(pid)}, {", ".join(sql_literal(values[c]) for c in cols)},
            NOW(), NOW()
        )
        ON CONFLICT (person_id) DO UPDATE SET
            {", ".join(f"{c} = {sql_literal(values[c])}" for c in cols)},
            updated_at = NOW()
        """
    )
    return pid


def record_touch(con: duckdb.DuckDBPyConnection, touch: dict) -> str:
    """Append one ``silver.fact_touch`` row; returns its touch_id.

    Deterministic id over the full event identity makes re-recording the
    same touch a no-op. ``touch_number`` sequences touches per person
    (0 for drafts with no linked person). Append-only: prior rows never
    mutate. ``recorded_at`` is the wall-clock insert time; ``event_date``
    is the business date from the source.
    """
    tid = _fact_id(
        touch.get("person_id"), touch.get("company_id"), touch.get("direction"),
        touch.get("channel"), touch.get("playbook"), touch.get("status"),
        touch.get("event_date"), touch.get("note"), touch.get("provenance"),
    )
    if _fact_exists(con, "fact_touch", "touch_id", tid):
        return tid
    if touch.get("person_id"):
        row = con.execute(
            "SELECT COALESCE(MAX(touch_number), 0) FROM silver.fact_touch "
            f"WHERE person_id = {sql_literal(touch.get('person_id'))}"
        ).fetchone()
        touch_number = int(row[0] or 0) + 1
    else:
        touch_number = 0
    con.execute(
        f"""
        INSERT INTO silver.fact_touch (
            touch_id, person_id, company_id, direction, channel, playbook,
            status, event_date, touch_number, note, provenance, recorded_at
        ) VALUES (
            {sql_literal(tid)}, {sql_literal(touch.get("person_id"))},
            {sql_literal(touch.get("company_id"))},
            {sql_literal(touch.get("direction"))},
            {sql_literal(touch.get("channel"))},
            {sql_literal(touch.get("playbook"))},
            {sql_literal(touch.get("status"))},
            {sql_literal(touch.get("event_date"))},
            {sql_literal(touch_number)},
            {sql_literal(touch.get("note"))},
            {sql_literal(touch.get("provenance"))}, NOW()
        )
        ON CONFLICT (touch_id) DO NOTHING
        """
    )
    return tid


def record_referral(con: duckdb.DuckDBPyConnection, ref: dict) -> str:
    """Append one ``silver.fact_referral`` row; returns its referral_id.

    Idempotent on the deterministic referral id (re-running is a no-op).
    """
    rid = _fact_id(
        ref.get("referrer_person_id"), ref.get("target_person_id"),
        ref.get("target_company_id"), ref.get("status"),
        ref.get("event_date"), ref.get("note"), ref.get("provenance"),
    )
    if _fact_exists(con, "fact_referral", "referral_id", rid):
        return rid
    con.execute(
        f"""
        INSERT INTO silver.fact_referral (
            referral_id, referrer_person_id, target_person_id,
            target_company_id, status, event_date, note, provenance, recorded_at
        ) VALUES (
            {sql_literal(rid)}, {sql_literal(ref.get("referrer_person_id"))},
            {sql_literal(ref.get("target_person_id"))},
            {sql_literal(ref.get("target_company_id"))},
            {sql_literal(ref.get("status"))},
            {sql_literal(ref.get("event_date"))},
            {sql_literal(ref.get("note"))},
            {sql_literal(ref.get("provenance"))}, NOW()
        )
        ON CONFLICT (referral_id) DO NOTHING
        """
    )
    return rid


def record_inbound(con: duckdb.DuckDBPyConnection, attr: dict) -> str:
    """Append one ``silver.fact_inbound_attribution`` row; returns its id.

    Idempotent on the deterministic attribution id (re-running is a no-op).
    """
    aid = _fact_id(
        attr.get("person_id"), attr.get("company_id"), attr.get("source_asset"),
        attr.get("event_date"), attr.get("note"), attr.get("provenance"),
    )
    if _fact_exists(con, "fact_inbound_attribution", "attribution_id", aid):
        return aid
    con.execute(
        f"""
        INSERT INTO silver.fact_inbound_attribution (
            attribution_id, person_id, company_id, source_asset, event_date,
            note, provenance, recorded_at
        ) VALUES (
            {sql_literal(aid)}, {sql_literal(attr.get("person_id"))},
            {sql_literal(attr.get("company_id"))},
            {sql_literal(attr.get("source_asset"))},
            {sql_literal(attr.get("event_date"))},
            {sql_literal(attr.get("note"))},
            {sql_literal(attr.get("provenance"))}, NOW()
        )
        ON CONFLICT (attribution_id) DO NOTHING
        """
    )
    return aid


def backfill_outreach_csv(con: duckdb.DuckDBPyConnection, csv_path) -> int:
    """Backfill the legacy outreach tracker CSV into BD facts; returns #new.

    Headers: date_found, company, name, title, linkedin_url, contact_type,
    agency, status, date_approved, date_sent, date_replied, outcome, notes.
    Each row upserts a dim_person and records a fact_touch with event_date =
    first non-empty of (date_sent, date_approved, date_found) and status via
    BD_CSV_STATUS_MAP (unknown -> 'drafted'). Companies get dim-company keys
    under source_board 'outreach' (a synthetic board: these rows come from
    the tracker, not a scrape board). Idempotent — re-running inserts 0.
    """
    import csv

    try:
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return 0
    if not rows:
        return 0

    inserted = 0
    for row in rows:
        name = (row.get("name") or "").strip()
        url = (row.get("linkedin_url") or "").strip()
        if not name and not url:
            continue
        company = (row.get("company") or "").strip()
        cid = company_id(normalize_company_name(company), "outreach") if company else ""
        pid = upsert_person(con, {
            "name": name or None,
            "linkedin_url": url or None,
            "title": (row.get("title") or "").strip() or None,
            "contact_type": (row.get("contact_type") or "").strip() or None,
            "agency": (row.get("agency") or "").strip() or None,
            "company_id": cid or None,
        })
        event_date = next(
            (row.get(k) for k in ("date_sent", "date_approved", "date_found")
             if (row.get(k) or "").strip()),
            "",
        )
        status = (row.get("status") or "").strip().lower()
        tid = _fact_id(
            pid, cid or None, "out", "linkedin", "cold-outreach",
            BD_CSV_STATUS_MAP.get(status, "drafted"), event_date,
            (row.get("notes") or "").strip() or None, "outreach_csv_backfill",
        )
        if _fact_exists(con, "fact_touch", "touch_id", tid):
            continue
        record_touch(con, {
            "person_id": pid,
            "company_id": cid or None,
            "direction": "out",
            "channel": "linkedin",
            "playbook": "cold-outreach",
            "status": BD_CSV_STATUS_MAP.get(status, "drafted"),
            "event_date": event_date or None,
            "note": (row.get("notes") or "").strip() or None,
            "provenance": "outreach_csv_backfill",
        })
        inserted += 1
    return inserted