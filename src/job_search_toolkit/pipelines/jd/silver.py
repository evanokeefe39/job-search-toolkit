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

from .config import ENRICHMENT_VERSION, WAREHOUSE_DB

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
    ("stock_symbol", "VARCHAR"),
    ("stock_exchange", "VARCHAR"),
    ("latest_funding_type", "VARCHAR"),
    ("latest_funding_amount_usd", "BIGINT"),
    ("homepage_url", "VARCHAR"),
    ("enriched_at", "TIMESTAMP"),
    ("enrichment_version", "INTEGER"),
]

# --- Enrichment stage gates -------------------------------------------------
# Each gate selects rows the stage still has to process. Column nullability /
# emptiness is the source of truth — there is no _enrichment flag anymore.

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
GATE_SCORE = "is_active AND overall_score IS NULL"

# Company research is dimension-scoped (one row per company, not per job).
# hiringcafe ships org_type from the source; LLM research never touches it.
DIM_COMPANY_GATE = "source_board <> 'hiringcafe' AND org_type IS NULL"

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

def connect() -> duckdb.DuckDBPyConnection:
    """Open the warehouse database (creating the file if missing)."""
    WAREHOUSE_DB.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(WAREHOUSE_DB))


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
        "enrichment_version": ENRICHMENT_VERSION,
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
    # is gone): backfill dim_company + company_id, then drop the JSON column.
    _migrate_company_info(con)


def _upsert_dim_companies(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    """Upsert dim_company rows on company_id (source data wins per run).

    New rows insert; existing rows keep LLM-researched fields (``org_type``,
    ``hq_country``, ``stock_*``, funding) unless the source provides a
    non-NULL value — scraper-parsed fields refresh, research output survives.
    """
    if not rows:
        return
    fields = [
        "name", "display_name", "source_board", "industry", "size_employees",
        "year_founded", "hq_country", "org_type", "stock_symbol",
        "stock_exchange", "latest_funding_type", "latest_funding_amount_usd",
        "homepage_url", "enrichment_version",
    ]
    json_fields = {"industry"}
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
                values.append(str(ENRICHMENT_VERSION))
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


def deactivate_not_seen(con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """Mark inactive every job not seen in ``run_id`` (the scrape of this run)."""
    con.execute(
        f'UPDATE silver.jobs SET is_active = FALSE, updated_at = NOW() '
        f'WHERE is_active AND last_seen_run <> {sql_literal(run_id)}'
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
            enrichment_version = {ENRICHMENT_VERSION}
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
    stale = f"enrichment_version < {ENRICHMENT_VERSION}"
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
            f"UPDATE silver.dim_company SET org_type = NULL, enriched_at = NULL "
            f"WHERE {stale} AND source_board <> 'hiringcafe'"
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
    rebuilt from ``dim_company`` (the same shape the fact table used to store
    as JSON) — callers keep reading ``job["company_info"]`` unchanged. Rows
    whose company_id has no dim row get ``company_info = {}``.
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
        # Column order matches DIM_COMPANY_COLUMNS (minus enriched_at).
        dims = con.execute(
            "SELECT company_id, name, display_name, industry, size_employees, "
            "year_founded, hq_country, org_type, stock_symbol, stock_exchange, "
            "latest_funding_type, latest_funding_amount_usd, homepage_url "
            "FROM silver.dim_company"
        ).fetchall()
        by_id = {r[0]: r for r in dims}
        for job in out:
            dim = by_id.get(job.get("company_id"))
            if dim is None:
                job["company_info"] = {}
                continue
            industry = dim[3]
            job["company_info"] = {
                "name": dim[2],  # display name, as written on the board
                "industry": json.loads(industry) if isinstance(industry, str) else industry,
                "size_employees": dim[4],
                "year_founded": dim[5],
                "hq_country": dim[6],
                "org_type": dim[7],
                "stock_symbol": dim[8],
                "stock_exchange": dim[9],
                "latest_funding_type": dim[10],
                "latest_funding_amount_usd": dim[11],
                "homepage_url": dim[12],
            }
    return out


