"""DuckDB gold layer for analytics over the merged canonical job dataset.

Reads the silver dataset (``data/silver/merged_jobs.json`` — a JSON list of
``CanonicalJob`` dicts) and loads it into a DuckDB database at
``data/gold/jobs.db``.

Layout:
- ``jobs``        — one row per job; top-level fields become columns, nested
  dicts/lists (salary, company_info, scores, _source, ...) are stored as
  JSON strings.
- ``ranked_jobs`` — scored jobs (overall_score + scores present), ordered by
  overall_score DESC.
- ``by_sector``   — job counts grouped by end_client_sector.
- ``by_tier``     — job counts grouped by recommendation_tier.

Idempotent: the table and all views are (re)created on every run, so calling
``build_gold`` twice yields the same database.

Usage:
    from job_search_toolkit.pipelines.jd.gold import build_gold
    build_gold(Path("data/silver/merged_jobs.json"), Path("data/gold/jobs.db"))
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

# Columns that must exist for the analytics views, per the CanonicalJob schema.
_SCORE_COL = "overall_score"
_SCORES_JSON_COL = "scores"
_SECTOR_COL = "end_client_sector"
_TIER_COL = "recommendation_tier"


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
    return "VARCHAR"


def _cell(value: Any) -> Any:
    """SQL cell value: nested containers become JSON strings, None stays NULL."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _sql_literal(value: Any) -> str:
    """Render a Python scalar as a DuckDB SQL literal.

    Used instead of bound parameters (``?`` placeholders), which deadlock in
    duckdb 1.5.5 on Windows/Python 3.14 (verified empirically). Strings are
    single-quoted with embedded quotes doubled — safe for arbitrary text.
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


def build_gold(silver_json: Path, db_path: Path) -> None:
    """Load ``silver_json`` (list of CanonicalJob dicts) into a DuckDB gold DB.

    Creates ``db_path`` and its parent directories if missing. Idempotent:
    the ``jobs`` table and the ``ranked_jobs``/``by_sector``/``by_tier`` views
    are replaced on every call.
    """
    silver_json = Path(silver_json)
    db_path = Path(db_path)

    jobs = json.loads(silver_json.read_text(encoding="utf-8"))
    if not isinstance(jobs, list):
        raise ValueError(
            f"{silver_json} must contain a JSON list of CanonicalJob dicts, "
            f"got {type(jobs).__name__}"
        )
    if not jobs:
        raise ValueError(f"{silver_json} contains no jobs; refusing to build an empty gold DB")

    # Top-level fields, in first-seen order (stable across runs for a given file).
    columns: list[str] = []
    seen: set[str] = set()
    for job in jobs:
        for key in job:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    if not columns:
        raise ValueError(f"{silver_json} contains jobs without any top-level fields")

    col_types = {
        col: _infer_column_type([job.get(col) for job in jobs]) for col in columns
    }
    ddl = ", ".join(f'"{col}" {col_types[col]}' for col in columns)

    rows = [
        tuple(_cell(job.get(col)) for col in columns)
        for job in jobs
    ]
    values_sql = ", ".join(
        "(" + ", ".join(_sql_literal(v) for v in row) + ")" for row in rows
    )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute(f"CREATE OR REPLACE TABLE jobs ({ddl})")
        con.execute(f"INSERT INTO jobs VALUES {values_sql}")

        con.execute(
            f"""
            CREATE OR REPLACE VIEW ranked_jobs AS
            SELECT *
            FROM jobs
            WHERE "{_SCORES_JSON_COL}" IS NOT NULL
              AND "{_SCORE_COL}" IS NOT NULL
            ORDER BY "{_SCORE_COL}" DESC
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE VIEW by_sector AS
            SELECT "{_SECTOR_COL}" AS end_client_sector,
                   COUNT(*) AS job_count
            FROM jobs
            GROUP BY "{_SECTOR_COL}"
            ORDER BY job_count DESC, "{_SECTOR_COL}"
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE VIEW by_tier AS
            SELECT "{_TIER_COL}" AS recommendation_tier,
                   COUNT(*) AS job_count
            FROM jobs
            GROUP BY "{_TIER_COL}"
            ORDER BY job_count DESC, "{_TIER_COL}"
            """
        )
