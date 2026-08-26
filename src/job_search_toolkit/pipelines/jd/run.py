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
    INGEST_ASSETS,
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


def run_ingest(run_id: str, board: str | None = None) -> bool:
    """Materialize the ingest recovery path for an explicit bronze run id.

    Runs ``silver_ingest`` (+ score/export/gold) against the given ``run_id``
    (optionally narrowed to one ``board``), reading the orphaned bronze from
    ``runs.json`` and upserting it — WITHOUT any scrape asset running. The
    selection materializes exactly ``INGEST_ASSETS``, so no board scrape or
    per-board silver is pulled in (``dg.materialize`` with an explicit
    selection does not expand the upstream closure). Raises ``ValueError``
    with the available runs/boards on an unknown run id / board.
    """
    from .assets.merge import ingest_bronze
    from .config import ensure_data_dirs
    from .silver import connect

    # Validate up front for a clean CLI error, before a (failed) Dagster run.
    ensure_data_dirs()
    with connect() as con:
        ingest_bronze(con, run_id, board)

    selection = dg.AssetSelection.assets(*INGEST_ASSETS)
    run_config = {
        "resources": {
            "ingest": {"config": {"run_id": run_id, "board": board or ""}}
        }
    }
    result = dg.materialize(ALL_ASSETS, selection=selection, run_config=run_config)
    print(f"SUCCESS: {result.success}")
    return result.success


def run_pipeline(
    enrich: bool = False,
    boards: list[str] | None = None,
    config_name: str = "default",
    max_pages: int | None = None,
) -> bool:
    """Materialize the DAG. Returns True on success.

    Default materializes the full ranking path (all active boards, no LLM).
    With ``enrich=True`` the optional enrichment assets run too. With ``boards``
    only those boards' scrape + ``silver_<board>`` assets run (plus the
    score/gold/exports), so a subset can be iterated without re-scraping the
    whole source set. Staleness (never deactivation) makes subset runs safe:
    boards not in the run simply keep their last ``last_seen_at``.

    ``config_name`` selects a named run config (``runs.<name>`` in config.yaml)
    driving timeouts/limits; ``max_pages`` caps each board's pages (None = no
    CLI override; 0 = unlimited). Both are threaded to the scrape assets via
    the ``RUN_CONFIG`` / ``RUN_MAX_PAGES`` env channels (matching the legacy
    ``MAX_PAGES`` mechanism) so in-process ``dg.materialize`` picks them up.
    """
    import os

    from .config import ensure_data_dirs

    ensure_data_dirs()

    # Thread the run selection into the scrape assets.
    os.environ["RUN_CONFIG"] = config_name
    if max_pages is not None:
        os.environ["RUN_MAX_PAGES"] = str(max_pages)
    else:
        os.environ.pop("RUN_MAX_PAGES", None)

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
