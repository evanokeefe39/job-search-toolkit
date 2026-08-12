"""Run the job search DAG.

Usage:
    job-search-toolkit pipeline run            # ranking path, zero LLM calls
    job-search-toolkit pipeline run --enrich   # + deferred LLM enrichment
    uv run python -m job_search_toolkit.pipelines.jd.run
"""
from __future__ import annotations

import dagster as dg
from dotenv import load_dotenv

load_dotenv()

from .definitions import ENRICH_ASSETS, RANKING_ASSETS


def run_pipeline(enrich: bool = False) -> bool:
    """Materialize the DAG. Returns True on success.

    Default materializes only the ranking path (no LLM). With ``enrich=True``
    the optional enrichment assets run too (translate/tech/classify/company
    research, dimension-scoped).
    """
    from .config import ensure_data_dirs

    ensure_data_dirs()
    assets = RANKING_ASSETS + (ENRICH_ASSETS if enrich else [])
    result = dg.materialize(assets)
    print(f"SUCCESS: {result.success}")
    return result.success


if __name__ == "__main__":
    run_pipeline()
