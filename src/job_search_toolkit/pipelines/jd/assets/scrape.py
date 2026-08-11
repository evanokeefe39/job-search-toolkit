"""Source assets: scrape job boards into canonical format."""


import json

import dagster as dg
from dagster import AssetExecutionContext

from .common import (
    FREEWORK_RAW,
    HIRINGCAFE_RAW,
    append_bronze_run,
    bronze_timestamped_path,
    iso_timestamp,
)
from ..config import ensure_data_dirs


def _write_bronze_snapshot(board: str, run_id: str, jobs: list[dict]) -> None:
    """Write this run's immutable bronze snapshot + manifest entry.

    The flat paths (``data/bronze/freework_jobs.json`` etc.) remain the
    live working files; the timestamped snapshot is the permanent record.
    """
    ts = iso_timestamp()
    ts_path = bronze_timestamped_path(board, ts)
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path.write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    append_bronze_run(run_id, board, ts, f"{board}/{ts_path.name}", len(jobs))


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from free-work.com (Paris tech/IT)",
)
def freework_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape free-work.com and normalize to canonical format."""
    from job_search_toolkit.scrapers.freework import (
        DEFAULT_CONTRACTS, DEFAULT_EXPERIENCE, DEFAULT_LOCATIONS,
        DEFAULT_QUERY, DEFAULT_RADIUS, DEFAULT_REMOTE, DEFAULT_SORT,
        build_url, scrape,
    )
    from ..adapt_freework import normalize_freework_job

    ensure_data_dirs()
    list_url = build_url(
        DEFAULT_QUERY, DEFAULT_LOCATIONS, DEFAULT_CONTRACTS,
        DEFAULT_REMOTE, DEFAULT_EXPERIENCE, DEFAULT_SORT, DEFAULT_RADIUS,
    )
    scrape(list_url, FREEWORK_RAW, max_pages=None, fmt="json")
    raw = json.loads(FREEWORK_RAW.read_text(encoding="utf-8"))
    canonical = [normalize_freework_job(j) for j in raw]
    FREEWORK_RAW.write_text(
        json.dumps(canonical, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_bronze_snapshot("freework", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from hiringcafe.com (Next.js SSR data route)",
)
def hiringcafe_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape hiringcafe.com and normalize to canonical format."""
    from job_search_toolkit.scrapers.hiringcafe import scrape

    ensure_data_dirs()
    scrape(output=HIRINGCAFE_RAW.with_suffix(""))
    canonical = json.loads(HIRINGCAFE_RAW.read_text(encoding="utf-8"))
    _write_bronze_snapshot("hiringcafe", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"path": str(HIRINGCAFE_RAW)})
