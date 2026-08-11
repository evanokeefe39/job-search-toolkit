"""Dagster definitions for the JD pipeline.

Assembles all assets into a single ``Definitions`` object. Import this from
``job_search_toolkit.pipelines.jd.definitions`` (or the package root).

Usage:
    from job_search_toolkit.pipelines.jd.definitions import defs
    defs.get_asset_job("full_pipeline").execute_in_process()
"""

from __future__ import annotations

import dagster as dg

from .assets import (
    freework_jobs,
    hiringcafe_jobs,
    silver_upsert,
    translated,
    tech_extracted,
    vertical_classified,
    company_stats,
    scored_jobs,
    ranked_csv,
    gold_views,
    merged_jobs_export,
    freework_enriched_export,
)

ALL_ASSETS = [
    freework_jobs,
    hiringcafe_jobs,
    silver_upsert,
    translated,
    tech_extracted,
    vertical_classified,
    company_stats,
    scored_jobs,
    ranked_csv,
    gold_views,
    merged_jobs_export,
    freework_enriched_export,
]

defs = dg.Definitions(assets=ALL_ASSETS)
