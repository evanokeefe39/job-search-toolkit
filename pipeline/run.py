"""Entry point: run the full job search DAG.

Usage:
    uv run python -m pipeline.run
"""
import dagster as dg
from dotenv import load_dotenv

load_dotenv()

from pipeline.assets import (
    freework_jobs, hiringcafe_jobs, merged_jobs,
    translated, tech_extracted, vertical_classified,
    company_stats, scored_jobs, ranked_csv,
)

ALL_ASSETS = [
    freework_jobs, hiringcafe_jobs, merged_jobs,
    translated, tech_extracted, vertical_classified,
    company_stats, scored_jobs, ranked_csv,
]

if __name__ == "__main__":
    result = dg.materialize(ALL_ASSETS)
    print(f"SUCCESS: {result.success}")
