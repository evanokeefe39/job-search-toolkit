"""Silver layer: DuckDB warehouse schema, upsert, and enrichment helpers.

The warehouse is a single DuckDB file (``data/warehouse/jobs.db``) with two
schemas:

- ``silver.jobs`` — one row per unique ``(id, source_board)`` (a job posted on
  both boards gets two rows — cross-board dedup is out of scope). Lineage
  columns track ``first_seen_*``/``last_seen_*``, ``is_active``,
  ``enriched_at`` and ``enrichment_version``. Jobs are never deleted;
  listings that disappear from the boards are marked inactive.
- ``gold.*`` — analytics views (see gold.py).

Enrichment state lives in column nullability, not in a ``_enrichment`` dict:
``description_language = 'fr'`` means "needs translation", an empty
``technologies`` array means "needs tech extraction", etc. A row is fully
enriched when all stage gates pass, at which point ``enriched_at`` is set
and the row becomes eligible for scoring.

DuckDB note: bound parameters (``?`` placeholders) deadlock in duckdb 1.5.5
on Windows/Python 3.14 (verified empirically), so all values are rendered
into SQL via ``sql_literal``/``sql_json``.
"""

from __future__ import annotations

import json
from typing import Any

import duckdb

from .config import ENRICHMENT_VERSION, WAREHOUSE_DB

# Pipeline-internal keys that never become warehouse columns.
_SKIP_KEYS = {"_enrichment", "_source"}

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
    "is_active AND source_board <> 'hiringcafe' AND engagement_type IS NULL"
)
GATE_COMPANY = (
    "is_active AND source_board <> 'hiringcafe' AND (company_info IS NULL "
    "OR json_extract_string(company_info, '$.org_type') IS NULL)"
)
GATE_SCORE = "is_active AND overall_score IS NULL"

# Complement of the gates — true when the stage's output is present.
DONE_TRANSLATE = "(description_language <> 'fr' OR TRIM(description_text) = '')"
DONE_TECH = "technologies IS NOT NULL"
DONE_CLASSIFY = (
    "source_board = 'hiringcafe' OR engagement_type IS NOT NULL"
)
DONE_COMPANY = (
    "source_board = 'hiringcafe' OR (company_info IS NOT NULL "
    "AND json_extract_string(company_info, '$.org_type') IS NOT NULL)"
)
DONE_ALL = (
    f"({DONE_TRANSLATE}) AND ({DONE_TECH}) AND ({DONE_CLASSIFY}) AND ({DONE_COMPANY})"
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


def ensure_jobs_table(con: duckdb.DuckDBPyConnection, jobs: list[dict]) -> list[str]:
    """Create ``silver.jobs`` if missing; ALTER ADD COLUMN for new fields.

    Returns the ordered column list: union of top-level keys across ``jobs``
    (minus ``_enrichment``/``_source``) plus the lineage columns.
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
            + [f'"{k}" {t}' for k, t in LINEAGE_COLUMNS]
        )
        con.execute(
            f'CREATE TABLE silver.jobs ({ddl}, PRIMARY KEY ("id", "source_board"))'
        )
    else:
        for key in keys:
            if key not in existing:
                con.execute(f'ALTER TABLE silver.jobs ADD COLUMN "{key}" {col_types[key]}')
    return keys + [k for k, _t in LINEAGE_COLUMNS]


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

    With ``full_replace`` (migration only), the source record's content
    replaces every column except ``id``, ``source_board``, ``first_seen*``
    and ``created_at`` — used when the incoming record is a superset of the
    stored one (e.g. backfilled CSV shadows upgraded with the full JSON).
    """
    insert_cols = ", ".join(f'"{k}"' for k in columns)
    rows_sql: list[str] = []
    for job in jobs:
        values: list[str] = []
        for key in columns:
            if key in ("first_seen_run", "last_seen_run"):
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
        con.execute(
            f"UPDATE silver.jobs SET company_info = '{{\"org_type\":\"unknown\"}}'::JSON "
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
) -> list[dict]:
    """Fetch rows as dicts with JSON columns decoded into Python values."""
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
    return out
