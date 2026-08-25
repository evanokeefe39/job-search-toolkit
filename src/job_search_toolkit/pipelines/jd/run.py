"""Run the job search DAG.

Usage:
    job-search-toolkit pipeline run                     # all boards, ranking path, zero LLM
    job-search-toolkit pipeline run --enrich            # + deferred LLM enrichment
    job-search-toolkit pipeline run --boards linkedin_jobs linkedin_posts
                                                        # subset: only those boards' scrape+ingest
    uv run python -m job_search_toolkit.pipelines.jd.run
"""
from __future__ import annotations

import dagster as dg
from dotenv import load_dotenv

load_dotenv()

from .definitions import (
    BOARD_SCRAPE_ASSETS,
    ENRICH_ASSETS,
    PIPELINE_ASSETS,
    RANKING_ASSETS,
)


def run_pipeline(
    enrich: bool = False, boards: list[str] | None = None
) -> bool:
    """Materialize the DAG. Returns True on success.

    Default materializes the full ranking path (all boards, no LLM). With
    ``enrich=True`` the optional enrichment assets run too. With ``boards``
    only those boards' scrape assets run (plus merge/score/export/gold), so a
    subset can be iterated without re-scraping the whole source set. Staleness
    (never deactivation) makes subset runs safe: boards not in the run simply
    keep their last ``last_seen_at``.
    """
    from .config import ensure_data_dirs

    ensure_data_dirs()
    assets = RANKING_ASSETS + (ENRICH_ASSETS if enrich else [])

    if boards:
        chosen = [BOARD_SCRAPE_ASSETS[b] for b in boards if b in BOARD_SCRAPE_ASSETS]
        unknown = [b for b in boards if b not in BOARD_SCRAPE_ASSETS]
        if unknown:
            raise ValueError(
                f"Unknown board(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(BOARD_SCRAPE_ASSETS))}"
            )
        if not chosen:
            raise ValueError("No boards selected.")
        # selection=[...] materializes only the chosen assets; upstream scrape
        # assets outside the selection are loaded, not re-scraped.
        selection = dg.AssetSelection.assets(*chosen) | dg.AssetSelection.assets(
            *PIPELINE_ASSETS
        )
        if enrich:
            selection = selection | dg.AssetSelection.assets(*ENRICH_ASSETS)
        result = dg.materialize(assets, selection=selection)
    else:
        result = dg.materialize(assets)

    print(f"SUCCESS: {result.success}")
    return result.success


if __name__ == "__main__":
    run_pipeline()
