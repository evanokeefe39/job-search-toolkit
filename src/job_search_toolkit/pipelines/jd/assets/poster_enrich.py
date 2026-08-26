"""LinkedIn post poster-location enrichment via the Apify profile actor.

The post adapter emits ``poster_name``/``poster_url`` from the post itself
(``normalize_linkedin_post``); this deferred, optional asset fills the poster's
``poster_location`` by scraping the poster's LinkedIn profile. It selects
``linkedin_posts`` rows via ``GATE_POSTER`` (a known poster URL whose location
is still NULL), scrapes each unique poster profile once, and UPDATEs the
location back. It is enrichment-only — never on the ranking path — and runs
via ``pipeline run --enrich``. Profile scrapes are pay-per-result (~$0.004 each)
so a row that never matches (private profile) stays NULL and costs nothing.
"""

import dagster as dg
from dagster import AssetExecutionContext

from .merge import SILVER_BOARD_ASSETS
from ..silver import GATE_POSTER, connect, fetch_jobs, sql_literal


@dg.asset(
    deps=list(SILVER_BOARD_ASSETS.values()),
    group_name="enrichment",
    description="Fill poster_location for linkedin_posts by scraping poster profiles",
)
def linkedin_post_poster_enriched(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape poster profiles and fill ``poster_location`` for queued posts."""
    from job_search_toolkit.linkedin.profile import LinkedInProfileScraper

    columns = ["id", "source_board", "poster_url"]
    with connect() as con:
        rows = fetch_jobs(con, columns, GATE_POSTER, order="id")

    unique_urls = sorted({r["poster_url"] for r in rows if r.get("poster_url")})
    if not unique_urls:
        return dg.MaterializeResult(metadata={"scraped": 0, "updated": 0})

    locations = LinkedInProfileScraper().scrape_locations(unique_urls)

    updated = 0
    with connect() as con:
        for row in rows:
            loc = locations.get(row.get("poster_url"))
            if not loc:
                continue
            con.execute(
                f'UPDATE silver.jobs SET poster_location = {sql_literal(loc)}, updated_at = NOW() '
                f'WHERE id = {sql_literal(row["id"])} '
                f'AND source_board = {sql_literal(row["source_board"])}'
            )
            updated += 1
    return dg.MaterializeResult(metadata={"scraped": len(unique_urls), "updated": updated})
