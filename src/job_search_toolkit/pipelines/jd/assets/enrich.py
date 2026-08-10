"""Enrichment assets: translate, extract tech, classify, company stats.

Each stage is idempotent — checks ``_enrichment`` flags, skips
already-processed jobs. Stages 1–3 are no-ops for HiringCafe data
(already English, tech pre-extracted, direct employer).
"""

from __future__ import annotations

import dagster as dg

from .common import load_merged, save_merged
from .merge import merged_jobs


@dg.asset(
    deps=[merged_jobs],
    group_name="enrichment",
    description="Translate French descriptions to English (no-op for hiringcafe)",
)
def translated() -> dg.MaterializeResult:
    """Translate French descriptions to English using LLM."""
    from ..enrich_canonical import translate_jobs as do_translate

    jobs = load_merged()
    do_translate(jobs)
    save_merged(jobs)
    en = sum(1 for j in jobs if j.get("description_language") == "en")
    return dg.MaterializeResult(metadata={"english": en, "total": len(jobs)})


@dg.asset(
    deps=[translated],
    group_name="enrichment",
    description="Extract technologies and competencies using LLM",
)
def tech_extracted() -> dg.MaterializeResult:
    """Extract technologies from descriptions using LLM."""
    from ..enrich_canonical import extract_tech as do_extract

    jobs = load_merged()
    do_extract(jobs)
    save_merged(jobs)
    done = sum(1 for j in jobs if j.get("_enrichment", {}).get("tech_extracted"))
    return dg.MaterializeResult(metadata={"extracted": done, "total": len(jobs)})


@dg.asset(
    deps=[translated],
    group_name="enrichment",
    description="Classify company vertical and engagement using LLM",
)
def vertical_classified() -> dg.MaterializeResult:
    """Classify company type and engagement using LLM."""
    from ..enrich_canonical import classify_jobs as do_classify

    jobs = load_merged()
    do_classify(jobs)
    save_merged(jobs)
    done = sum(1 for j in jobs if j.get("_enrichment", {}).get("vertical_classified"))
    return dg.MaterializeResult(metadata={"classified": done, "total": len(jobs)})


@dg.asset(
    deps=[vertical_classified],
    group_name="enrichment",
    description="Research company stats using LLM",
)
def company_stats() -> dg.MaterializeResult:
    """Mark company research. HiringCafe pre-enriched, freework deferred."""
    jobs = load_merged()
    for job in jobs:
        enrichment = job.setdefault("_enrichment", {})
        ci = job.get("company_info", {})
        if enrichment.get("company_researched") and ci.get("org_type") != "unknown":
            continue
        if job.get("source_board") == "hiringcafe":
            enrichment["company_researched"] = True
            continue
        enrichment["company_researched"] = False
    save_merged(jobs)
    done = sum(1 for j in jobs if j.get("_enrichment", {}).get("company_researched"))
    return dg.MaterializeResult(metadata={"researched": done, "total": len(jobs)})
