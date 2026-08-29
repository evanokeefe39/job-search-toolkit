"""Score and export assets: score pending jobs, export ranked CSV.

Scoring is incremental: only rows with ``overall_score IS NULL`` are scored.
The score stage is decoupled from LLM enrichment — ``scored_jobs`` depends
on the per-board silver assets (and ``silver_ingest`` for the resume-from-
bronze path) and reads purely tabular fields (company data via the
``dim_company`` join, which at scoring time contains only scraper-parsed
values, never LLM research). The ranked CSV export is a backward-compat
bridge — it is materialized from ``silver.jobs`` on every run so the
jd-refresh / new-application skills keep working unchanged.
"""

import csv

import dagster as dg
from dagster import AssetExecutionContext

from .merge import SILVER_BOARD_ASSETS, silver_ingest
from ..silver import GATE_SCORE, connect, fetch_jobs, reset_stale, sql_json, sql_literal

# Every field score_engine.py reads (score_engine.py::score_jobs).
# company_info is NOT fetched as a column — it is rebuilt from dim_company by
# fetch_jobs(join_company=True).
SCORE_COLUMNS = [
    "id", "source_board", "title", "description_text",
    "salary", "workplace_type", "contract_types", "contract_duration",
    "seniority_level", "role_category", "technologies",
    "posting_company_type", "engagement_type",
    "date_posted", "last_seen_at",
]

# Columns this asset writes. silver.jobs is created from incoming bronze
# columns, which don't include scoring outputs — so on a fresh or partial
# warehouse (e.g. the ingest path) these may not exist yet. They are
# guaranteed here so reset_stale/_update never reference a missing column.
_SCORING_COLUMNS = [
    ("scores", "JSON"),
    ("overall_score", "DOUBLE"),
    ("recommendation_tier", "VARCHAR"),
]


def _ensure_scoring_columns(con) -> None:
    """Idempotently add the scoring-output columns if the table lacks them."""
    exists = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'silver' AND table_name = 'jobs'"
    ).fetchone()[0]
    if not exists:
        return
    existing = {r[1] for r in con.execute("PRAGMA table_info('silver.jobs')").fetchall()}
    for name, typ in _SCORING_COLUMNS:
        if name not in existing:
            con.execute(f'ALTER TABLE silver.jobs ADD COLUMN "{name}" {typ}')


def _update(con, job: dict, sets: str) -> None:
    """Run an UPDATE for one row, quoting id/source_board."""
    con.execute(
        f'UPDATE silver.jobs SET {sets}, updated_at = NOW() '
        f'WHERE id = {sql_literal(job["id"])} '
        f'AND source_board = {sql_literal(job["source_board"])}'
    )


@dg.asset(
    deps=list(SILVER_BOARD_ASSETS.values()) + [silver_ingest],
    group_name="scoring",
    description="Score pending jobs and assign recommendation tiers",
)
def scored_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Score all pending jobs using the canonical field-based scoring.

    Purely tabular: no LLM enrichment dependency. Company data comes from the
    ``dim_company`` join (scraper-parsed fields only at this point).
    """
    from ..score_engine import score_jobs as do_score

    with connect() as con:
        _ensure_scoring_columns(con)
        reset_stale(con, "score")
        # A partial/ingest warehouse may lack some canonical columns (a bronze
        # record only creates the columns it carries). score_engine tolerates
        # missing fields (neutral), so fetch only the columns that exist.
        existing = {r[1] for r in con.execute("PRAGMA table_info('silver.jobs')").fetchall()}
        cols = [c for c in SCORE_COLUMNS if c in existing]
        rows = fetch_jobs(con, cols, GATE_SCORE, order="id", join_company=True)
        do_score(rows)
        updated = 0
        for job in rows:
            if job.get("overall_score") is None:
                continue  # scoring failed — stays pending for next run
            _update(con, job, (
                f"scores = {sql_json(job['scores'])}, "
                f"overall_score = {sql_literal(job['overall_score'])}, "
                f"recommendation_tier = {sql_literal(job['recommendation_tier'])}"
            ))
            updated += 1

    return dg.MaterializeResult(metadata={"scored": updated, "pending": len(rows)})


@dg.asset(
    deps=[scored_jobs],
    group_name="scoring",
    description="Export scored jobs to ranked CSV (backward-compat bridge)",
)
def ranked_csv(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Export scored, ranked active jobs to CSV (same columns as before)."""
    from ..config import SILVER_DIR

    with connect() as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info('silver.jobs')").fetchall()]
        jobs = fetch_jobs(con, cols, "is_active", join_company=True)

    jobs_sorted = sorted(
        jobs, key=lambda j: j.get("overall_score") or 0, reverse=True
    )

    fieldnames = [
        "overall_score", "recommendation_tier", "source_board",
        "scores_pay", "scores_flexibility", "scores_low_responsibility",
        "scores_tech_match",
        "title", "company", "apply_url",
        "location_raw", "workplace_type", "date_posted",
        "salary_min_annual_eur", "salary_max_annual_eur",
        "seniority_level", "role_category", "years_experience_min",
        "contract_types", "contract_duration",
        "technologies", "competencies",
        "engagement_type", "posting_company_type",
        "end_client_name", "end_client_sector",
        "company_industry", "company_size", "company_founded",
        "company_type", "company_stock_symbol",
    ]
    csv_path = SILVER_DIR / "jobs_ranked.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for job in jobs_sorted:
            s = job.get("scores") or {}
            ci = job.get("company_info") or {}
            salary = job.get("salary") or {}
            row = {
                "overall_score": job.get("overall_score"),
                "recommendation_tier": job.get("recommendation_tier"),
                "source_board": job.get("source_board"),
                "scores_pay": s.get("pay"),
                "scores_flexibility": s.get("flexibility"),
                "scores_low_responsibility": s.get("low_responsibility"),
                "scores_tech_match": s.get("tech_match"),
                "title": job.get("title"),
                "company": job.get("company"),
                "apply_url": job.get("apply_url"),
                "location_raw": job.get("location_raw"),
                "workplace_type": job.get("workplace_type"),
                "date_posted": job.get("date_posted"),
                "salary_min_annual_eur": salary.get("min_annual_eur"),
                "salary_max_annual_eur": salary.get("max_annual_eur"),
                "seniority_level": job.get("seniority_level"),
                "role_category": job.get("role_category"),
                "years_experience_min": job.get("years_experience_min"),
                "contract_types": "|".join(job.get("contract_types", [])),
                "contract_duration": job.get("contract_duration"),
                "technologies": "|".join(job.get("technologies", [])),
                "competencies": "|".join(job.get("competencies", [])),
                "engagement_type": job.get("engagement_type"),
                "posting_company_type": job.get("posting_company_type"),
                "end_client_name": job.get("end_client_name"),
                "end_client_sector": job.get("end_client_sector"),
                "company_industry": "|".join(ci.get("industry", [])),
                "company_size": ci.get("size_employees"),
                "company_founded": ci.get("year_founded"),
                "company_type": ci.get("org_type"),
                "company_stock_symbol": ci.get("stock_symbol"),
            }
            writer.writerow(row)

    return dg.MaterializeResult(metadata={
        "total_exported": len(jobs_sorted),
        "path": str(csv_path),
    })
