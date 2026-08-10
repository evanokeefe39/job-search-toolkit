"""
Dagster asset graph for the multi-board job search pipeline.

Assets flow: scrape → merge → enrich → score → export.
Each enrichment stage is idempotent — it checks `_enrichment` flags and
skips already-processed jobs. Stages 1–3 are no-ops for HiringCafe data
(already English, tech pre-extracted, direct employer).

Run:
    uv run dagster asset materialize -m pipeline.assets -a scored_jobs
    uv run dagster asset materialize -m pipeline.assets -a ranked_csv

Or from Python:
    from pipeline.assets import defs
    defs.get_asset_job("full_pipeline").execute_in_process()
"""

from __future__ import annotations


import dagster as dg

from .config import BRONZE_DIR, SILVER_DIR, ensure_data_dirs

# ---------------------------------------------------------------------------
# Paths (medallion layout: bronze = raw canonical, silver = merged/enriched)
# ---------------------------------------------------------------------------
FREEWORK_RAW = BRONZE_DIR / "freework_jobs.json"
HIRINGCAFE_RAW = BRONZE_DIR / "hiringcafe_jobs.json"
MERGED_JOBS = SILVER_DIR / "merged_jobs.json"
RANKED_CSV = SILVER_DIR / "jobs_ranked.csv"


# ---------------------------------------------------------------------------
# Source assets — scrape job boards
# ---------------------------------------------------------------------------

@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from free-work.com (Paris tech/IT)",
)
def freework_jobs() -> dg.MaterializeResult:
    """Scrape free-work.com and normalize to canonical format."""
    import json

    from ..scrapers.freework import (
        DEFAULT_CONTRACTS, DEFAULT_EXPERIENCE, DEFAULT_LOCATIONS,
        DEFAULT_QUERY, DEFAULT_RADIUS, DEFAULT_REMOTE, DEFAULT_SORT,
        build_url, scrape,
    )
    from .adapt_freework import normalize_freework_job

    ensure_data_dirs()
    list_url = build_url(
        DEFAULT_QUERY, DEFAULT_LOCATIONS, DEFAULT_CONTRACTS,
        DEFAULT_REMOTE, DEFAULT_EXPERIENCE, DEFAULT_SORT, DEFAULT_RADIUS,
    )
    scrape(list_url, FREEWORK_RAW, max_pages=None, fmt="json")
    raw = json.loads(FREEWORK_RAW.read_text(encoding="utf-8"))
    canonical = [normalize_freework_job(j) for j in raw]
    FREEWORK_RAW.write_text(json.dumps(canonical, indent=2, ensure_ascii=False), encoding="utf-8")
    return dg.MaterializeResult(metadata={"total": len(canonical)})

@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from hiringcafe.com (Next.js SSR data route)",
)
def hiringcafe_jobs() -> dg.MaterializeResult:
    """Scrape hiringcafe.com and normalize to canonical format."""
    from ..scrapers.hiringcafe import scrape

    ensure_data_dirs()
    # The scraper writes <output>.json + <output>.csv; use the raw path base.
    scrape(output=HIRINGCAFE_RAW.with_suffix(""))
    return dg.MaterializeResult(metadata={"path": str(HIRINGCAFE_RAW)})


# ---------------------------------------------------------------------------
# Merge asset
# ---------------------------------------------------------------------------

@dg.asset(
    deps=[freework_jobs, hiringcafe_jobs],
    group_name="processing",
    description="Merged canonical jobs from all boards, deduplicated",
)
def merged_jobs() -> dg.MaterializeResult:
    """Load all source files, merge, deduplicate by id."""
    import json

    all_jobs: list[dict] = []
    seen: set[str] = set()
    sources_used: list[str] = []

    for path, label in [
        (FREEWORK_RAW, "freework"),
        (HIRINGCAFE_RAW, "hiringcafe"),
    ]:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for job in data:
                jid = job.get("id", "")
                if jid and jid in seen:
                    continue
                if jid:
                    seen.add(jid)
                all_jobs.append(job)
        sources_used.append(f"{label}: {len(data) if isinstance(data, list) else 1}")

    MERGED_JOBS.write_text(json.dumps(all_jobs, indent=2, ensure_ascii=False), encoding="utf-8")
    return dg.MaterializeResult(metadata={
        "total_jobs": len(all_jobs),
        "sources": ", ".join(sources_used),
    })


# ---------------------------------------------------------------------------
# Enrichment assets — each checks _enrichment flags, skips if done
# ---------------------------------------------------------------------------

def _load_merged() -> list[dict]:
    import json
    return json.loads(MERGED_JOBS.read_text(encoding="utf-8"))


def _save_merged(jobs: list[dict]) -> None:
    import json
    MERGED_JOBS.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


@dg.asset(
    deps=[merged_jobs],
    group_name="enrichment",
    description="Translate French descriptions to English (no-op for hiringcafe)",
)
def translated() -> dg.MaterializeResult:
    """Translate French descriptions to English using LLM."""
    from .enrich_canonical import translate_jobs as do_translate
    jobs = _load_merged()
    do_translate(jobs)
    _save_merged(jobs)
    en = sum(1 for j in jobs if j.get("description_language") == "en")
    return dg.MaterializeResult(metadata={"english": en, "total": len(jobs)})


@dg.asset(
    deps=[translated],
    group_name="enrichment",
    description="Extract technologies and competencies using LLM",
)
def tech_extracted() -> dg.MaterializeResult:
    """Extract technologies from descriptions using LLM."""
    from .enrich_canonical import extract_tech as do_extract
    jobs = _load_merged()
    do_extract(jobs)
    _save_merged(jobs)
    done = sum(1 for j in jobs if j.get("_enrichment", {}).get("tech_extracted"))
    return dg.MaterializeResult(metadata={"extracted": done, "total": len(jobs)})


@dg.asset(
    deps=[translated],
    group_name="enrichment",
    description="Classify company vertical and engagement using LLM",
)
def vertical_classified() -> dg.MaterializeResult:
    """Classify company type and engagement using LLM."""
    from .enrich_canonical import classify_jobs as do_classify
    jobs = _load_merged()
    do_classify(jobs)
    _save_merged(jobs)
    done = sum(1 for j in jobs if j.get("_enrichment", {}).get("vertical_classified"))
    return dg.MaterializeResult(metadata={"classified": done, "total": len(jobs)})


@dg.asset(
    deps=[vertical_classified],
    group_name="enrichment",
    description="Research company stats using LLM",
)
def company_stats() -> dg.MaterializeResult:
    """Mark company research. HiringCafe pre-enriched, freework deferred."""
    jobs = _load_merged()
    for job in jobs:
        enrichment = job.setdefault("_enrichment", {})
        ci = job.get("company_info", {})
        # Reset stale flag: freework jobs with unknown company info aren't done
        if enrichment.get("company_researched") and ci.get("org_type") != "unknown":
            continue
        if job.get("source_board") == "hiringcafe":
            enrichment["company_researched"] = True
            continue
        enrichment["company_researched"] = False
    _save_merged(jobs)
    done = sum(1 for j in jobs if j.get("_enrichment", {}).get("company_researched"))
    return dg.MaterializeResult(metadata={"researched": done, "total": len(jobs)})


# ---------------------------------------------------------------------------
# Score and export
# ---------------------------------------------------------------------------

@dg.asset(
    deps=[tech_extracted, vertical_classified, company_stats],
    group_name="scoring",
    description="Score all jobs and assign recommendation tiers",
)
def scored_jobs() -> dg.MaterializeResult:
    """Score all jobs using the canonical field-based scoring functions."""
    jobs = _load_merged()

    from .stage5_score_analyze import score_jobs as do_score

    # Run scorer (idempotent, updates in-place)
    do_score(jobs)

    # Mark scored
    scored_count = 0
    for job in jobs:
        enrichment = job.setdefault("_enrichment", {})
        if not enrichment.get("scored", False):
            enrichment["scored"] = True
            scored_count += 1

    _save_merged(jobs)

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
    jobs = _load_merged()
    # Sort by overall_score descending
    jobs_sorted = sorted(
        jobs, key=lambda j: j.get("overall_score") or 0, reverse=True
    )

    import csv

    # Flatten canonical fields for CSV
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


# ---------------------------------------------------------------------------
# Definitions — all assets registered
# ---------------------------------------------------------------------------

defs = dg.Definitions(
    assets=[
        freework_jobs,
        hiringcafe_jobs,
        merged_jobs,
        translated,
        tech_extracted,
        vertical_classified,
        company_stats,
        scored_jobs,
        ranked_csv,
    ],
)
