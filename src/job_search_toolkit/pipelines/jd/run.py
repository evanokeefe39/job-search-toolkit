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
    ALL_ASSETS,
    BOARD_SCRAPE_ASSETS,
    ENRICH_ASSETS,
    RANKING_ASSETS,
    SILVER_BOARD_ASSETS,
)
from .assets import (
    freework_enriched_export,
    gold_views,
    merged_jobs_export,
    ranked_csv,
    scored_jobs,
)

# Downstream assets that consume silver: score + export + gold. When a subset
# (``--boards``) is selected these run once after the chosen boards' silvers.
DOWNSTREAM_ASSETS = [
    scored_jobs,
    ranked_csv,
    gold_views,
    merged_jobs_export,
    freework_enriched_export,
]


def _boards_selection(boards: list[str], enrich: bool = False) -> dg.AssetSelection:
    """Asset selection for a ``--boards`` subset run.

    Selects the chosen boards' scrape assets AND their ``silver_<board>``
    assets plus the downstream score/gold/export assets. Assets outside the
    selection (other boards' scrapes/silvers, and enrichment unless requested)
    are loaded rather than re-materialized, so selecting ``silver_<board>``
    never drags in another board's scrape.
    """
    chosen_scrapes = [
        BOARD_SCRAPE_ASSETS[b] for b in boards if b in BOARD_SCRAPE_ASSETS
    ]
    chosen_silvers = [
        SILVER_BOARD_ASSETS[b] for b in boards if b in SILVER_BOARD_ASSETS
    ]
    selection = (
        dg.AssetSelection.assets(*chosen_scrapes)
        | dg.AssetSelection.assets(*chosen_silvers)
        | dg.AssetSelection.assets(*DOWNSTREAM_ASSETS)
    )
    if enrich:
        selection = selection | dg.AssetSelection.assets(*ENRICH_ASSETS)
    return selection


def run_pipeline(
    enrich: bool = False, boards: list[str] | None = None
) -> bool:
    """Materialize the DAG. Returns True on success.

    Default materializes the full ranking path (all active boards, no LLM).
    With ``enrich=True`` the optional enrichment assets run too. With ``boards``
    only those boards' scrape + ``silver_<board>`` assets run (plus the
    score/gold/exports), so a subset can be iterated without re-scraping the
    whole source set. Staleness (never deactivation) makes subset runs safe:
    boards not in the run simply keep their last ``last_seen_at``.
    """
    from .config import ensure_data_dirs

    ensure_data_dirs()

    if boards:
        unknown = [b for b in boards if b not in BOARD_SCRAPE_ASSETS]
        if unknown:
            raise ValueError(
                f"Unknown board(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(BOARD_SCRAPE_ASSETS))}"
            )
        if not any(b in BOARD_SCRAPE_ASSETS for b in boards):
            raise ValueError("No boards selected.")
        # Pass the full registry so any opt-in board (e.g. datasciencejobs) can
        # be selected; the selection restricts what actually materializes.
        selection = _boards_selection(boards, enrich=enrich)
        result = dg.materialize(ALL_ASSETS, selection=selection)
    else:
        assets = RANKING_ASSETS + (ENRICH_ASSETS if enrich else [])
        result = dg.materialize(assets)

    print(f"SUCCESS: {result.success}")
    return result.success


if __name__ == "__main__":
    run_pipeline()
