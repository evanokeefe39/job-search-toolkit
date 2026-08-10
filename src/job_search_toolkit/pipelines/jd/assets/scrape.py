"""Source assets: scrape job boards into canonical format."""

from __future__ import annotations

import json

import dagster as dg

from .common import FREEWORK_RAW, HIRINGCAFE_RAW
from ..config import ensure_data_dirs


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from free-work.com (Paris tech/IT)",
)
def freework_jobs() -> dg.MaterializeResult:
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
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from hiringcafe.com (Next.js SSR data route)",
)
def hiringcafe_jobs() -> dg.MaterializeResult:
    """Scrape hiringcafe.com and normalize to canonical format."""
    from job_search_toolkit.scrapers.hiringcafe import scrape

    ensure_data_dirs()
    scrape(output=HIRINGCAFE_RAW.with_suffix(""))
    return dg.MaterializeResult(metadata={"path": str(HIRINGCAFE_RAW)})
