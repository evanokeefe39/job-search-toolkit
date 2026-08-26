"""LinkedIn post enrichment asset: LLM gap-fill for posts regex couldn't fit.

The regex pass (``linkedin/posts_extract`` -> ``normalize_linkedin_post``)
leaves posts whose verdict is ``queue`` with an empty ``title`` and/or
``location_raw``. This deferred, optional LLM asset selects exactly those rows
via ``GATE_POST_ENRICH`` (silver.py), fills the gaps with one LLM call per post, and
UPDATEs the filled fields back to ``silver.jobs``. It is incremental (the
empty-based gate never re-selects an already-filled row) and never runs on the
ranking path.
"""

import asyncio

import dagster as dg
from dagster import AssetExecutionContext

from .merge import SILVER_BOARD_ASSETS
from ..silver import (
    GATE_POST_ENRICH,
    connect,
    fetch_jobs,
    mark_enriched,
    sql_json,
    sql_literal,
)


def _update(con, job: dict, sets: str) -> None:
    """Run an UPDATE for one row, quoting id/source_board."""
    con.execute(
        f'UPDATE silver.jobs SET {sets}, updated_at = NOW() '
        f'WHERE id = {sql_literal(job["id"])} '
        f'AND source_board = {sql_literal(job["source_board"])}'
    )


@dg.asset(
    deps=list(SILVER_BOARD_ASSETS.values()),
    group_name="enrichment",
    description="Fill title/location for queued linkedin_posts via LLM",
)
def linkedin_post_enriched(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Fill title/location gaps for linkedin_posts that regex couldn't fit."""
    from ..post_enrich_canonical import enrich_posts
    from ..resources.llm_client import LLMClient

    columns = [
        "id",
        "source_board",
        "title",
        "location_raw",
        "description_text",
        "company",
    ]

    async def _run() -> tuple[int, int]:
        client = LLMClient()
        try:
            with connect() as con:
                rows = fetch_jobs(
                    con, columns, GATE_POST_ENRICH, order="id",
                )
            await enrich_posts(rows, client)
            updated = 0
            with connect() as con:
                for job in rows:
                    if not job.get("_enrichment", {}).get("post_enriched"):
                        continue  # LLM failed — stays pending for next run
                    _update(con, job, (
                        f"title = {sql_literal(job['title'])}, "
                        f"location_raw = {sql_literal(job['location_raw'])}, "
                        f"role_category = {sql_literal(job['role_category'])}, "
                        f"seniority_level = {sql_literal(job['seniority_level'])}, "
                        f"engagement_type = {sql_literal(job['engagement_type'])}, "
                        f"end_client_name = {sql_literal(job['end_client_name'])}, "
                        f"end_client_sector = {sql_literal(job['end_client_sector'])}, "
                        f"competencies = {sql_json(job['competencies'])}"
                    ))
                    updated += 1
                mark_enriched(con)
            return updated, len(rows)
        finally:
            await client.close()

    updated, pending = asyncio.run(_run())
    return dg.MaterializeResult(metadata={"enriched": updated, "pending": pending})
