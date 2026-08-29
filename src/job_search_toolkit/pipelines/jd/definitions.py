"""Dagster definitions for the JD pipeline.

Two jobs:
- ``full_pipeline`` — the ranking path (scrape → upsert → score → export →
  gold). Materializes with zero LLM calls.
- ``enrich_job`` — the optional, deferred LLM enrichment pass (translate,
  tech extraction, classify, dimension-scoped company research). Reachable
  via ``pipeline run --enrich`` or explicit asset selection.

``ALL_ASSETS`` is the full registry (both jobs' assets plus the opt-in
``datasciencejobs`` board) and remains the single source for tests and
tooling that enumerate assets.

Usage:
    from job_search_toolkit.pipelines.jd.definitions import defs
    defs.get_asset_job("full_pipeline").execute_in_process()
"""

from __future__ import annotations

import dagster as dg

from .assets import (
    linkedin_post_enriched,
    linkedin_post_poster_enriched,
    translated,
    tech_extracted,
    vertical_classified,
    dim_company_enriched,
    scored_jobs,
    warehouse_outcomes,
    ranked_csv,
    gold_views,
    merged_jobs_export,
    freework_enriched_export,
)
from .assets.scrape import BOARD_SCRAPE_ASSETS
from .assets.merge import SILVER_BOARD_ASSETS, silver_ingest

# Boards on the default ranking path. Opt-in boards (datasciencejobs — long,
# brittle; wttj/builtin — large or bot-protected, see plans) are deliberately
# excluded but reachable via `--boards <name>`.
OPT_IN_BOARDS = frozenset({"datasciencejobs", "wttj", "builtin"})
RANKING_BOARDS = tuple(b for b in BOARD_SCRAPE_ASSETS if b not in OPT_IN_BOARDS)

# Ranking path: scrape -> upsert -> score -> outcome sync -> export -> gold.
# warehouse_outcomes is deterministic/offline (no LLM) and gold's
# score_calibration (upcoming slice) reads fresh outcomes.
RANKING_ASSETS = (
    [BOARD_SCRAPE_ASSETS[b] for b in RANKING_BOARDS]
    + [SILVER_BOARD_ASSETS[b] for b in RANKING_BOARDS]
    + [scored_jobs, warehouse_outcomes, ranked_csv, gold_views, merged_jobs_export, freework_enriched_export]
)

# Per-board silver assets (scored_jobs/gold/export depend on them), used by
# `pipeline run --boards <name>` to select the subset's ingest + downstream.
PIPELINE_ASSETS = (
    [SILVER_BOARD_ASSETS[b] for b in RANKING_BOARDS]
    + [scored_jobs, warehouse_outcomes, ranked_csv, gold_views, merged_jobs_export, freework_enriched_export]
)

# Deferred LLM enrichment: optional, never on the ranking path.
ENRICH_ASSETS = [
    translated,
    tech_extracted,
    vertical_classified,
    dim_company_enriched,
    linkedin_post_enriched,
    linkedin_post_poster_enriched,
]

# Ingest-only recovery path: silver_ingest (explicit run_id) + the downstream
# score/export/gold assets. Selecting these with `dg.materialize` runs exactly
# this set (no scrape, no per-board silver), so `pipeline ingest --run-id R`
# recovers an orphaned bronze snapshot offline.
INGEST_ASSETS = (
    [silver_ingest]
    + [scored_jobs, warehouse_outcomes, ranked_csv, gold_views, merged_jobs_export, freework_enriched_export]
)

# Opt-in boards are in the registry (so `--boards <name>` can select their
# scrape + silver asset) but not part of either named job, keeping them off the
# default pipeline.
ALL_ASSETS = (
    RANKING_ASSETS
    + [
        asset
        for b in sorted(OPT_IN_BOARDS)
        for asset in (BOARD_SCRAPE_ASSETS[b], SILVER_BOARD_ASSETS[b])
    ]
    + [silver_ingest]
    + ENRICH_ASSETS
)

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
        dg.define_asset_job(
            "ingest_job",
            selection=dg.AssetSelection.assets(*INGEST_ASSETS),
        ),
    ],
)
