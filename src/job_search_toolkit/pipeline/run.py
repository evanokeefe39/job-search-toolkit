"""Run the full job search DAG.

Usage:
    job-search-toolkit pipeline run
    uv run python -m job_search_toolkit.pipeline.run
"""
import dagster as dg
from dotenv import load_dotenv

load_dotenv()

from .assets import (  # noqa: E402
    freework_jobs, hiringcafe_jobs, merged_jobs,
    translated, tech_extracted, vertical_classified,
    company_stats, scored_jobs, ranked_csv,
)

ALL_ASSETS = [
    freework_jobs, hiringcafe_jobs, merged_jobs,
    translated, tech_extracted, vertical_classified,
    company_stats, scored_jobs, ranked_csv,
]


def run_pipeline() -> bool:
    """Materialize the full DAG. Returns True on success."""
    from .config import ensure_data_dirs

    ensure_data_dirs()
    result = dg.materialize(ALL_ASSETS)
    print(f"SUCCESS: {result.success}")
    return result.success


if __name__ == "__main__":
    run_pipeline()
