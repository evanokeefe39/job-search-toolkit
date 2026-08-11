"""Gold views asset: (re)create the analytics views after each pipeline run."""

import dagster as dg
from dagster import AssetExecutionContext

from .score import scored_jobs
from ..config import WAREHOUSE_DB
from ..gold import build_gold


@dg.asset(
    deps=[scored_jobs],
    group_name="analytics",
    description="Create or replace gold analytics views over silver.jobs",
)
def gold_views(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Rebuild the gold views, baking this run into the run-scoped views."""
    build_gold(WAREHOUSE_DB, run_id=context.run_id)
    return dg.MaterializeResult(metadata={"db": str(WAREHOUSE_DB)})
