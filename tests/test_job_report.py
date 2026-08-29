"""Tests for the per-job report module (Epic 5.3).

Builds a real ``silver.jobs`` table in a temp DuckDB via the pipeline's own
``ensure_jobs_table`` so ``fetch_job`` is verified against the REAL schema
(PK is ``(id, source_board)``), then checks rendering rules: missing data is
always rendered as "unknown", never fabricated.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from job_search_toolkit.pipelines.jd.silver import ensure_jobs_table
from job_search_toolkit.report import fetch_job, render_job_report


FULL_JOB: dict = {
    "id": "abc123",
    "source_board": "freework",
    "title": "Senior Data Engineer",
    "company": "Acme Corp",
    "location_raw": "Paris, France",
    "workplace_type": "hybrid",
    "date_posted": "2026-08-20",
    "salary": {"is_disclosed": True, "min_annual_eur": 60000.0, "max_annual_eur": 75000.0},
    "contract_types": ["full_time"],
    "seniority_level": "senior",
    "years_experience_min": 5,
    "technologies": ["Python", "DuckDB", "dbt"],
    "scores": {"pay": 0.8, "flexibility": 0.5, "low_responsibility": 0.7,
               "tech_match": 0.9, "freshness": 0.95},
    "overall_score": 0.812,
    "recommendation_tier": "top",
    "company_info": {"org_type": "product", "industry": "Software"},
}


def _make_db(tmp_path: Path, jobs: list[dict]) -> Path:
    """Create a temp warehouse with silver.jobs + dim_company populated.

    Each job gets a ``company_id`` = ``dim_<id>``; a matching row is written
    to ``silver.dim_company`` so ``fetch_job``'s join resolves. ``industry``
    is stored JSON-encoded because ``fetch_job`` runs ``json.loads`` on it.
    """
    db = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db))
    rows = [{**j, "company_id": f"dim_{j['id']}"} for j in jobs]
    # ensure_jobs_table already adds company_id as a column, so it must not
    # appear in the job dicts used to derive the schema.
    schema_jobs = [{k: v for k, v in r.items() if k != "company_id"} for r in rows]
    columns = ensure_jobs_table(con, schema_jobs)
    con.execute(
        "CREATE TABLE silver.dim_company "
        "(company_id VARCHAR, org_type VARCHAR, industry VARCHAR)"
    )
    for job in rows:
        # A dim_company row exists only when the job carries company_info —
        # a job without it must exercise the "no dim row" path (unknown).
        ci = job.get("company_info")
        if ci:
            con.execute(
                "INSERT INTO silver.dim_company VALUES (?, ?, ?)",
                [job["company_id"], ci.get("org_type"),
                 json.dumps(ci.get("industry")) if ci.get("industry") else None],
            )
        vals = []
        for col in columns:
            v = job.get(col)
            if isinstance(v, (dict, list)):
                vals.append(f"'{json.dumps(v)}'::JSON")
            elif v is None:
                vals.append("NULL")
            elif isinstance(v, str):
                vals.append("'" + v.replace("'", "''") + "'")
            else:
                vals.append(str(v))
        con.execute(
            f"INSERT INTO silver.jobs ({', '.join(chr(34) + c + chr(34) for c in columns)}) "
            f"VALUES ({', '.join(vals)})"
        )
    con.close()
    return db


def test_per_job_report_missing_fields() -> None:
    """A job with no salary/company/technologies/scores renders 'unknown'
    for those fields — never a guess or fabricated value."""
    report = render_job_report({"id": "x1", "source_board": "freework", "title": "Dev"})
    assert "unknown" in report
    assert "Acme" not in report  # nothing invented
    # gaps section exists and lists the missing features
    assert "gaps" in report.lower()
    assert "- technologies" in report


def test_full_featured_job_renders_deterministic_features() -> None:
    """A full-featured job dict renders its deterministic features."""
    report = render_job_report(FULL_JOB)
    assert "Senior Data Engineer" in report
    assert "Acme Corp" in report
    assert "Paris, France" in report
    assert "hybrid" in report
    assert "freework" in report
    assert "2026-08-20" in report
    # salary range rendered
    assert "60000" in report and "75000" in report
    # contract, level, experience
    assert "full_time" in report
    assert "senior" in report
    assert "5" in report
    # tech stack
    assert "Python" in report and "DuckDB" in report and "dbt" in report
    # fit scores
    assert "tech_match" in report and "0.9" in report
    assert "0.812" in report
    assert "top" in report
    # company quality
    assert "product" in report  # org_type
    assert "Software" in report


def test_fetch_job_unknown_id_returns_none(tmp_path: Path) -> None:
    """fetch_job returns None for an unknown id (temp duckdb, no network)."""
    db = _make_db(tmp_path, [FULL_JOB])
    assert fetch_job(db, "nope-not-there") is None


def test_fetch_job_roundtrip(tmp_path: Path) -> None:
    """fetch_job returns the stored row with JSON columns decoded."""
    db = _make_db(tmp_path, [FULL_JOB])
    job = fetch_job(db, "abc123")
    assert job is not None
    assert job["title"] == "Senior Data Engineer"
    assert isinstance(job["salary"], dict)
    assert job["salary"]["min_annual_eur"] == 60000.0
    assert isinstance(job["technologies"], list)
    assert job["technologies"] == ["Python", "DuckDB", "dbt"]
    assert job["scores"]["tech_match"] == 0.9
    assert job["company_info"]["org_type"] == "product"
    assert job["company_info"]["industry"] == "Software"


def test_report_deterministic_byte_identical() -> None:
    """Same input -> byte-identical report."""
    assert render_job_report(FULL_JOB) == render_job_report(FULL_JOB)
    assert render_job_report({}) == render_job_report({})


def test_fetch_job_missing_company_dim_is_unknown(tmp_path: Path) -> None:
    """A job whose company_id has no dim_company row still renders, with
    company quality marked unknown."""
    job = {k: v for k, v in FULL_JOB.items() if k != "company_info"}
    db = _make_db(tmp_path, [job])
    fetched = fetch_job(db, "abc123")
    assert fetched is not None
    assert fetched.get("company_info") == {}
    report = render_job_report(fetched)
    assert "unknown" in report
    assert "org_type: unknown" in report
