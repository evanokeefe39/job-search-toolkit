"""JD pipeline assets — one module per pipeline stage."""

from .scrape import (
    datasciencejobs_jobs,
    englishjobs_jobs,
    faruse_jobs,
    freework_jobs,
    hellowork_jobs,
    hiringcafe_jobs,
    linkedin_jobs,
    linkedin_posts,
    remoteok_jobs,
    wwr_jobs,
    BOARD_SCRAPE_ASSETS,
)
from .merge import (
    SILVER_BOARD_ASSETS,
    silver_ingest,
    ingest_bronze,
    list_runs,
    IngestConfig,
    ingest_config,
)
from .enrich import (
    translated, tech_extracted, vertical_classified, dim_company_enriched,
    dim_company_news_enriched,
)
from .post_enrich import linkedin_post_enriched
from .poster_enrich import linkedin_post_poster_enriched
from .score import scored_jobs, ranked_csv
from .gold import gold_views
from .outcomes import sync_outcomes, warehouse_outcomes
from .exports import merged_jobs_export, freework_enriched_export
__all__ = [
    "datasciencejobs_jobs",
    "freework_jobs",
    "hiringcafe_jobs",
    "hellowork_jobs",
    "englishjobs_jobs",
    "faruse_jobs",
    "wwr_jobs",
    "remoteok_jobs",
    "linkedin_jobs",
    "linkedin_posts",
    "linkedin_post_enriched",
    "linkedin_post_poster_enriched",
    "BOARD_SCRAPE_ASSETS",
    "SILVER_BOARD_ASSETS",
    "silver_ingest",
    "ingest_bronze",
    "list_runs",
    "IngestConfig",
    "ingest_config",
    "translated",
    "tech_extracted",
    "dim_company_enriched",
    "dim_company_news_enriched",
    "vertical_classified",
    "scored_jobs",
    "warehouse_outcomes",
    "sync_outcomes",
    "gold_views",
    "merged_jobs_export",
    "freework_enriched_export",
]
