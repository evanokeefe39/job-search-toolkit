"""Incremental company golden-record resolution asset.

Resolves NEW fact-table company names only (exact -> stem -> fuzzy-auto ->
human-review queue) against the durable ``silver.company_alias`` registry,
via ``company_resolve.resolve_new_names`` + ``write_review_queue``. Already-
resolved names short-circuit (idempotent re-runs are no-ops).

This asset is deliberately OFF the zero-LLM ranking dependency graph:
``scored_jobs``/``ranked_csv`` do NOT depend on it. It is registered in
``definitions.ALL_ASSETS`` only, so it is runnable on explicit selection
(``dagster asset materialize --select company_names_resolved``) but never
materializes on ``pipeline run``.
"""

import dagster as dg
from dagster import AssetExecutionContext

from ..company_resolve import resolve_new_names, write_review_queue
from ..silver import connect


@dg.asset(
    deps=[],
    group_name="resolution",
    description=(
        "Incremental company golden-record resolution: NEW names only "
        "(exact/stem/fuzzy-auto ladder + human review queue). Off the "
        "ranking path — never materialized by the zero-LLM pipeline run."
    ),
)
def company_names_resolved(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Resolve fact-table company names the alias registry does not know."""
    with connect() as con:
        stats = resolve_new_names(con)
        review_pending = write_review_queue(con)
    context.log.info(
        "company resolution: %s (review pairs pending: %d)", stats, review_pending
    )
    return dg.MaterializeResult(
        metadata={
            "resolved": stats.get("resolved", 0),
            "self_seeded": stats.get("self_seeded", 0),
            "rekeyed": stats.get("rekeyed", 0),
            "review": stats.get("review", 0),
            "review_pairs_pending": review_pending,
        }
    )
