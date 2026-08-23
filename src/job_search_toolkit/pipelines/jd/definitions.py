"""Dagster definitions for the JD pipeline.

Two jobs:
- ``full_pipeline`` — the ranking path (scrape → upsert → score → export →
  gold). Materializes with zero LLM calls.
- ``enrich_job`` — the optional, deferred LLM enrichment pass (translate,
  tech extraction, classify, dimension-scoped company research). Reachable
  via ``pipeline run --enrich`` or explicit asset selection.

``ALL_ASSETS`` is the full registry (both jobs' assets) and remains the
single source for tests and tooling that enumerate assets.

Usage:
    from job_search_toolkit.pipelines.jd.definitions import defs
    defs.get_asset_job("full_pipeline").execute_in_process()
"""

from __future__ import annotations

import dagster as dg

from .assets import (
    datasciencejobs_jobs,
    englishjobs_jobs,
    faruse_jobs,
    freework_jobs,
    hellowork_jobs,
    hiringcafe_jobs,
    linkedin_jobs,
    linkedin_post_enriched,
    linkedin_posts,
    remoteok_jobs,
    wwr_jobs,
    silver_upsert,
    translated,
    tech_extracted,
    vertical_classified,
    dim_company_enriched,
    scored_jobs,
    ranked_csv,
    gold_views,
    merged_jobs_export,
    freework_enriched_export,
)

# Ranking path: scrape -> upsert -> score -> export -> gold. No LLM assets.
RANKING_ASSETS = [
    freework_jobs,
    hiringcafe_jobs,
    hellowork_jobs,
    englishjobs_jobs,
    faruse_jobs,
    wwr_jobs,
    remoteok_jobs,
    datasciencejobs_jobs,
    linkedin_jobs,
    linkedin_posts,
    silver_upsert,
    scored_jobs,
    ranked_csv,
    gold_views,
    merged_jobs_export,
    freework_enriched_export,
]

# Deferred LLM enrichment: optional, never on the ranking path.
ENRICH_ASSETS = [
    translated,
    tech_extracted,
    vertical_classified,
    dim_company_enriched,
    linkedin_post_enriched,
]

ALL_ASSETS = RANKING_ASSETS + ENRICH_ASSETS

defs = dg.Definitions(
    assets=ALL_ASSETS,
    jobs=[
        dg.define_asset_job(
            "full_pipeline",
            selection=dg.AssetSelection.assets(*RANKING_ASSETS),
        ),
        dg.define_asset_job(
            "enrich_job",
            selection=dg.AssetSelection.assets(*ENRICH_ASSETS),
        ),
    ],
)
