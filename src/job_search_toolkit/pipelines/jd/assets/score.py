"""Score and export assets: score jobs, export ranked CSV."""

from __future__ import annotations

import csv

import dagster as dg

from .common import load_merged, save_merged, RANKED_CSV
from .enrich import tech_extracted, vertical_classified, company_stats


@dg.asset(
    deps=[tech_extracted, vertical_classified, company_stats],
    group_name="scoring",
    description="Score all jobs and assign recommendation tiers",
)
def scored_jobs() -> dg.MaterializeResult:
    """Score all jobs using the canonical field-based scoring functions."""
    from ..score_engine import score_jobs as do_score

    jobs = load_merged()

    # Run scorer (idempotent, updates in-place)
    do_score(jobs)

    # Mark scored
    scored_count = 0
    for job in jobs:
        enrichment = job.setdefault("_enrichment", {})
        if not enrichment.get("scored", False):
            enrichment["scored"] = True
            scored_count += 1

    save_merged(jobs)

    tiers: dict[str, int] = {}
    for j in jobs:
        t = j.get("recommendation_tier", "low")
        tiers[t] = tiers.get(t, 0) + 1

    return dg.MaterializeResult(metadata={
        "scored": scored_count,
        **{f"tier_{k}": v for k, v in tiers.items()},
    })


@dg.asset(
    deps=[scored_jobs],
    group_name="scoring",
    description="Export scored jobs to ranked CSV",
)
def ranked_csv() -> dg.MaterializeResult:
    """Export scored, ranked jobs to CSV."""
    jobs = load_merged()
    jobs_sorted = sorted(
        jobs, key=lambda j: j.get("overall_score") or 0, reverse=True
    )

    fieldnames = [
        "overall_score", "recommendation_tier", "source_board",
        "scores_pay", "scores_flexibility", "scores_low_responsibility",
        "scores_tech_match", "scores_company_quality",
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

    with open(RANKED_CSV, "w", newline="", encoding="utf-8") as f:
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
                "scores_company_quality": s.get("company_quality"),
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
        "path": str(RANKED_CSV),
    })
