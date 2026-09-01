"""Per-job dossier: fetch a warehouse job and render a deterministic report.

Pure functions — no dagster. Missing data is rendered as ``unknown``;
this module NEVER fabricates a value (repo rule: unknown is written as
"researched, nothing found", NULL means "not yet processed").

Real warehouse schema (see pipelines/jd/silver.py):
- ``silver.jobs`` PK is ``(id, source_board)``; nested columns (salary,
  scores, contract_types, technologies, industry, ...) are typed JSON.
- ``LINEAGE_COLUMNS`` carry date_posted/last_seen_at-style recency facts.
- ``dim_company`` (keyed by ``company_id`` FK) carries org_type / industry.
Score fields (pipelines/jd/score_engine.py::score_jobs): pay, flexibility,
low_responsibility, tech_match, freshness — plus
``overall_score`` and ``recommendation_tier`` (top/high/medium/low) stored
as sibling columns.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
from .pipelines.jd import db

UNKNOWN = "unknown"

# JSON-typed columns in silver.jobs (see silver.py: canonical nested fields).
_JSON_COLUMNS = {"salary", "scores", "contract_types", "technologies", "industry", "location"}


def _fmt(v) -> str:
    """Render a scalar for the report; None/empty -> 'unknown'."""
    if v is None or v == "":
        return UNKNOWN
    return str(v)


def _fmt_list(v) -> str:
    """Render a list value; None/empty list -> 'unknown'."""
    if not v:
        return UNKNOWN
    if isinstance(v, str):
        return v
    return ", ".join(str(x) for x in v)


def fetch_job(db_path: Path, job_id: str) -> dict | None:
    """Fetch one silver.jobs row by ``id`` with JSON columns decoded.

    Returns None when the id is unknown. Joins ``dim_company`` org_type and
    industry via the ``company_id`` FK when present (into ``company_info``);
    a missing dim row yields ``company_info = {}``.

    The same ``id`` may exist across multiple ``source_board`` rows (the PK
    is ``(id, source_board)``); the lowest ``source_board`` is returned
    deterministically (ORDER BY source_board LIMIT 1).
    """
    con = db.connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM silver.jobs WHERE id = ? ORDER BY source_board LIMIT 1",
            [job_id],
        ).fetchall()
        if not rows:
            return None
        col_names = [d[0] for d in con.description]
        row = dict(zip(col_names, rows[0]))
        for col in _JSON_COLUMNS:
            if isinstance(row.get(col), str):
                try:
                    row[col] = json.loads(row[col])
                except (json.JSONDecodeError, TypeError):
                    pass
        row["company_info"] = {}
        if row.get("company_id"):
            dims = con.execute(
                "SELECT company_type, industry FROM silver.dim_company WHERE company_id = ?",
                [row["company_id"]],
            ).fetchall()
            if dims:
                company_type, industry = dims[0]
                row["company_info"] = {
                    "company_type": company_type,
                    "industry": json.loads(industry) if isinstance(industry, str) else industry,
                }
        return row
    finally:
        con.close()


def render_job_report(job: dict) -> str:
    """Render a deterministic human-readable dossier for one job.

    Missing features render as ``unknown`` and are collected in an explicit
    Gaps section. No LLM, no fabrication.
    """
    salary = job.get("salary") or {}
    scores = job.get("scores") or {}
    company_info = job.get("company_info") or {}
    gaps: list[str] = []

    def flag(feature: str, value: str) -> str:
        if value == UNKNOWN:
            gaps.append(feature)
        return value

    lines: list[str] = []
    lines.append("=== Job Report ===")
    lines.append(f"id: {_fmt(job.get('id'))}")
    lines.append(f"title: {_fmt(job.get('title'))}")
    lines.append(f"company: {_fmt(job.get('company'))}")
    lines.append(f"location: {flag('location', _fmt(job.get('location_raw')))}")
    lines.append(f"workplace_type: {flag('workplace_type', _fmt(job.get('workplace_type')))}")
    lines.append(f"source_board: {_fmt(job.get('source_board'))}")
    lines.append(f"date_posted: {flag('date_posted', _fmt(job.get('date_posted')))}")
    if job.get("days_since_posted") is not None:
        lines.append(f"days_since_posted: {job['days_since_posted']}")

    lines.append("")
    lines.append("--- Compensation ---")
    if salary.get("is_disclosed") and (salary.get("min_annual_eur") is not None
                                       or salary.get("max_annual_eur") is not None):
        lines.append(f"salary: {salary.get('min_annual_eur', UNKNOWN)} - "
                     f"{salary.get('max_annual_eur', UNKNOWN)} EUR/year")
    else:
        lines.append(f"salary: {flag('salary', UNKNOWN)}")

    lines.append("")
    lines.append("--- Contract & Level ---")
    lines.append(f"contract_types: {flag('contract_types', _fmt_list(job.get('contract_types')))}")
    lines.append(f"seniority_level: {flag('seniority_level', _fmt(job.get('seniority_level')))}")
    lines.append(f"years_experience_min: "
                 f"{flag('years_experience_min', _fmt(job.get('years_experience_min')))}")

    lines.append("")
    lines.append("--- Fit ---")
    if scores:
        for k in ("pay", "flexibility", "low_responsibility", "tech_match",
                  "freshness"):
            lines.append(f"{k}: {_fmt(scores.get(k))}")
    else:
        lines.append(f"{flag('scores', UNKNOWN)}")
    lines.append(f"overall_score: {_fmt(job.get('overall_score'))}")
    lines.append(f"recommendation_tier: {flag('recommendation_tier', _fmt(job.get('recommendation_tier')))}")

    lines.append("")
    lines.append("--- Tech Stack ---")
    lines.append(f"technologies: {flag('technologies', _fmt_list(job.get('technologies')))}")

    lines.append("")
    lines.append("--- Company Quality ---")
    lines.append(f"company_type: {flag('company_type', _fmt(company_info.get('company_type')))}")
    lines.append(f"industry: {flag('industry', _fmt_list(company_info.get('industry')))}")

    lines.append("")
    lines.append("--- Gaps ---")
    if gaps:
        lines.extend(f"- {g}" for g in gaps)
    else:
        lines.append("(none — all features present)")
    lines.append("")
    return "\n".join(lines)
