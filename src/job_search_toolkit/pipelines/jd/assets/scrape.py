"""Source assets: scrape job boards into canonical format."""


import json
import os

import dagster as dg
from dagster import AssetExecutionContext

from .common import (
    DATASCIENCEJOBS_RAW,
    ENGLISHJOBS_RAW,
    FARUSE_RAW,
    FREEWORK_RAW,
    HELLOWORK_RAW,
    HIRINGCAFE_RAW,
    REMOTEOK_RAW,
    WWR_RAW,
    append_bronze_run,
    bronze_timestamped_path,
    iso_timestamp,
)
from job_search_toolkit.run_config import load_run_config
from ..config import ensure_data_dirs


def _max_pages() -> int | None:
    """Global pagination override (0/empty = unlimited).

    Precedence: pipeline CLI ``--max-pages`` (via the ``RUN_MAX_PAGES`` env
    channel set by run_pipeline) > config.yaml (``max_pages`` under the
    selected run) > legacy ``MAX_PAGES`` env > None (unlimited). Returns the
    resolved cap, or None when unlimited.
    """
    name = os.getenv("RUN_CONFIG", "default")
    cli_raw = os.getenv("RUN_MAX_PAGES")
    cli = int(cli_raw) if cli_raw not in (None, "") else None
    return load_run_config(name, max_pages=cli).max_pages


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
    scrape(list_url, FREEWORK_RAW, max_pages=_max_pages(), fmt="json")
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
    mp = _max_pages()
    # Only pass a global cap when one is set; otherwise hiringcafe uses its own
    # default cap (RunConfig hiringcafe_max_pages, 50).
    kwargs = {"output": HIRINGCAFE_RAW.with_suffix("")}
    if mp is not None:
        kwargs["max_pages"] = mp
    scrape(**kwargs)
    canonical = json.loads(HIRINGCAFE_RAW.read_text(encoding="utf-8"))
    _write_bronze_snapshot("hiringcafe", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"path": str(HIRINGCAFE_RAW)})


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from hellowork.com (French general board)",
)
def hellowork_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape hellowork.com and normalize to canonical format."""
    from job_search_toolkit.scrapers.hellowork import (
        DEFAULT_CONTRACTS, DEFAULT_LOCATION, DEFAULT_QUERY,
        build_url, scrape,
    )

    ensure_data_dirs()
    list_url = build_url(DEFAULT_QUERY, DEFAULT_LOCATION, DEFAULT_CONTRACTS)
    scrape(list_url, HELLOWORK_RAW, _max_pages(), "json")
    canonical = json.loads(HELLOWORK_RAW.read_text(encoding="utf-8"))
    _write_bronze_snapshot("hellowork", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from englishjobs.fr (English jobs in France)",
)
def englishjobs_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape englishjobs.fr and normalize to canonical format."""
    from job_search_toolkit.scrapers.englishjobs import (
        DEFAULT_LOCATION, DEFAULT_QUERY,
        build_url, scrape,
    )

    ensure_data_dirs()
    list_url = build_url(DEFAULT_QUERY, DEFAULT_LOCATION)
    scrape(list_url, ENGLISHJOBS_RAW, _max_pages(), "json")
    canonical = json.loads(ENGLISHJOBS_RAW.read_text(encoding="utf-8"))
    _write_bronze_snapshot("englishjobs", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from faruse.com (English jobs in Europe)",
)
def faruse_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape faruse.com and normalize to canonical format."""
    from job_search_toolkit.scrapers.faruse import (
        DEFAULT_LOCATION, DEFAULT_QUERY,
        build_url, scrape,
    )

    ensure_data_dirs()
    url = build_url(DEFAULT_QUERY, DEFAULT_LOCATION, [], [], [], "")
    scrape(url, FARUSE_RAW, _max_pages(), "json", DEFAULT_QUERY, DEFAULT_LOCATION)
    canonical = json.loads(FARUSE_RAW.read_text(encoding="utf-8"))
    _write_bronze_snapshot("faruse", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from weworkremotely.com (remote-only RSS feeds)",
)
def wwr_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape weworkremotely.com RSS feeds and normalize to canonical format."""
    from job_search_toolkit.scrapers.weworkremotely import (
        FEED_URLS, DEFAULT_QUERY,
        scrape,
    )

    ensure_data_dirs()
    scrape(list(FEED_URLS), WWR_RAW, "json", DEFAULT_QUERY, _max_pages())
    canonical = json.loads(WWR_RAW.read_text(encoding="utf-8"))
    _write_bronze_snapshot("wwr", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from remoteok.com (public JSON API)",
)
def remoteok_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape remoteok.com and normalize to canonical format."""
    from job_search_toolkit.scrapers.remoteok import (
        DEFAULT_LOCATION, DEFAULT_QUERY,
        scrape,
    )

    ensure_data_dirs()
    scrape(DEFAULT_QUERY, DEFAULT_LOCATION, REMOTEOK_RAW, "json")
    canonical = json.loads(REMOTEOK_RAW.read_text(encoding="utf-8"))
    _write_bronze_snapshot("remoteok", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from datasciencejobs.com (data-only board)",
)
def datasciencejobs_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape datasciencejobs.com and normalize to canonical format."""
    from job_search_toolkit.scrapers.datasciencejobs import (
        DEFAULT_LOCATION, DEFAULT_QUERY,
        build_url, scrape,
    )

    ensure_data_dirs()
    list_url = build_url(DEFAULT_QUERY, DEFAULT_LOCATION)
    scrape(list_url, DATASCIENCEJOBS_RAW, _max_pages(), "json", DEFAULT_QUERY)
    canonical = json.loads(DATASCIENCEJOBS_RAW.read_text(encoding="utf-8"))
    _write_bronze_snapshot("datasciencejobs", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


def _has_discovery_key() -> bool:
    """Return True when a LinkedIn discovery token is present in the env.

    Post: True if any of APIFY_TOKEN / APIFY_API_TOKEN / TAVILY_API_KEY is
    set (non-empty); otherwise False.
    """
    return any(os.getenv(key) for key in ("APIFY_TOKEN", "APIFY_API_TOKEN", "TAVILY_API_KEY"))


@dg.asset(
    group_name="sources",
    description="LinkedIn job listings (/jobs/view/) discovered via the LinkedIn source adapter",
)
def linkedin_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Discover + normalize LinkedIn job listings into canonical format."""
    from job_search_toolkit.scrapers.linkedin.adapter import run_discovery
    from job_search_toolkit.scrapers.linkedin.config import LinkedInConfig
    from ..adapt_linkedin import normalize_linkedin_job

    if not _has_discovery_key():
        context.log.warning(
            "No LinkedIn discovery token (APIFY_TOKEN/APIFY_API_TOKEN/TAVILY_API_KEY); "
            "writing empty linkedin_jobs snapshot."
        )
        _write_bronze_snapshot("linkedin_jobs", context.run_id, [])
        return dg.MaterializeResult(metadata={"total": 0})

    ensure_data_dirs()
    config = LinkedInConfig.from_preferences()
    outcome = run_discovery(config, kinds=["job"])
    canonical = [normalize_linkedin_job(job) for job in outcome.jobs]
    _write_bronze_snapshot("linkedin_jobs", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="LinkedIn recruiter posts (/posts/) discovered via the LinkedIn source adapter",
)
def linkedin_posts(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Discover + normalize LinkedIn recruiter posts into canonical format."""
    from job_search_toolkit.scrapers.linkedin.adapter import run_discovery
    from job_search_toolkit.scrapers.linkedin.config import LinkedInConfig
    from ..adapt_linkedin import normalize_linkedin_post

    if not _has_discovery_key():
        context.log.warning(
            "No LinkedIn discovery token (APIFY_TOKEN/APIFY_API_TOKEN/TAVILY_API_KEY); "
            "writing empty linkedin_posts snapshot."
        )
        _write_bronze_snapshot("linkedin_posts", context.run_id, [])
        return dg.MaterializeResult(metadata={"total": 0})

    ensure_data_dirs()
    config = LinkedInConfig.from_preferences()
    outcome = run_discovery(config, kinds=["post"])
    canonical = [
        job
        for job in (normalize_linkedin_post(post) for post in outcome.posts)
        if job is not None
    ]
    _write_bronze_snapshot("linkedin_posts", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


# CLI board name -> scrape asset, for `pipeline run --boards <name> ...`.
# The board name doubles as the bronze ``board`` field each asset writes (e.g.
# board ``linkedin_jobs`` -> bronze ``board=linkedin_jobs``), so the per-board
# silver reader (merge.py) can match a scrape to its bronze entry by name.
# datasciencejobs is deliberately excluded from the default ranking path
# (long-running, brittle — see ISSUES.md) but reachable as an explicit opt-in
# via `--boards datasciencejobs`.
BOARD_SCRAPE_ASSETS: dict[str, dg.AssetsDefinition] = {
    "freework": freework_jobs,
    "hiringcafe": hiringcafe_jobs,
    "hellowork": hellowork_jobs,
    "englishjobs": englishjobs_jobs,
    "faruse": faruse_jobs,
    "wwr": wwr_jobs,
    "remoteok": remoteok_jobs,
    "datasciencejobs": datasciencejobs_jobs,
    "linkedin_jobs": linkedin_jobs,
    "linkedin_posts": linkedin_posts,
}
