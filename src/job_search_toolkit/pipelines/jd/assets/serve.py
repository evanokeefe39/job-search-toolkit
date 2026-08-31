"""Serving-mirror sync asset: trigger the quack server to refresh serve.db."""

import dagster as dg
from dagster import AssetExecutionContext

from pathlib import Path

from .gold import gold_views

def _repo_root() -> Path:
    """The directory containing src/job_search_toolkit/pipelines/jd/assets/."""
    for p in Path(__file__).resolve().parents:
        if (p / "src" / "job_search_toolkit" / "pipelines" / "jd" / "assets").is_dir():
            return p
    return Path(__file__).resolve().parents[5]


REFRESH_FLAG = _repo_root() / "data" / "_quack" / "refresh.flag"


@dg.asset(
    deps=[gold_views],
    group_name="serve",
    description="Touch data/_quack/refresh.flag so the quack server refreshes serve.db from jobs.db",
)
def serve_refresh(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Signal the quack server to self-refresh the serving mirror."""
    REFRESH_FLAG.parent.mkdir(parents=True, exist_ok=True)
    REFRESH_FLAG.touch()
    return dg.MaterializeResult(metadata={"refresh_flag": str(REFRESH_FLAG.relative_to(_repo_root()))})
