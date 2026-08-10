"""Run the full job search DAG.

Usage:
    job-search-toolkit pipeline run
    uv run python -m job_search_toolkit.pipelines.jd.run
"""
import dagster as dg
from dotenv import load_dotenv

load_dotenv()

from .definitions import ALL_ASSETS


def run_pipeline() -> bool:
    """Materialize the full DAG. Returns True on success."""
    from .config import ensure_data_dirs

    ensure_data_dirs()
    result = dg.materialize(ALL_ASSETS)
    print(f"SUCCESS: {result.success}")
    return result.success


if __name__ == "__main__":
    run_pipeline()
