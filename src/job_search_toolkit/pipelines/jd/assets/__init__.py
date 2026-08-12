"""JD pipeline assets — one module per pipeline stage."""

from .scrape import (
    datasciencejobs_jobs,
    englishjobs_jobs,
    faruse_jobs,
    freework_jobs,
    hellowork_jobs,
    hiringcafe_jobs,
    remoteok_jobs,
    wwr_jobs,
)
from .merge import silver_upsert
from .enrich import translated, tech_extracted, vertical_classified, dim_company_enriched
from .score import scored_jobs, ranked_csv
from .gold import gold_views
from .exports import merged_jobs_export, freework_enriched_export
__all__ = [
    "freework_jobs",
    "hiringcafe_jobs",
    "hellowork_jobs",
    "englishjobs_jobs",
    "faruse_jobs",
    "wwr_jobs",
    "remoteok_jobs",
    "datasciencejobs_jobs",
    "silver_upsert",
    "translated",
    "tech_extracted",
    "dim_company_enriched",
    "vertical_classified",
    "scored_jobs",
    "ranked_csv",
    "gold_views",
    "merged_jobs_export",
    "freework_enriched_export",
]
