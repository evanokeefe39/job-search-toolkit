"""Source assets: scrape job boards into canonical format."""


import json
import os

import dagster as dg
import httpx
from curl_cffi.requests.exceptions import RequestException as CurlRequestException
from dagster import AssetExecutionContext

from .common import (
    BUILTIN_RAW,
    DATASCIENCEJOBS_RAW,
    ENGLISHJOBS_RAW,
    FARUSE_RAW,
    FREEWORK_RAW,
    HELLOWORK_RAW,
    HIRINGCAFE_RAW,
    REMOTEOK_RAW,
    WWR_RAW,
    WTTJ_RAW,
    append_bronze_run,
    append_bronze_trip,
    bronze_timestamped_path,
    iso_timestamp,
)
from job_search_toolkit.run_config import get_run_config, load_run_config
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
def trip_guard(board: str):
    """Per-run source circuit breaker for a board scrape asset.

    Wraps the asset's compute so a SOURCE-level failure trips THIS board for
    THIS run instead of aborting the whole Dagster run: the exception is
    logged, recorded in the per-run trip manifest (``data/bronze/trips.json``),
    and the asset returns a normal ``MaterializeResult`` with a ``tripped``
    marker rather than raising. The per-board silver reader then sees no bronze
    for the tripped board and no-ops, so ``scored_jobs``/gold still run on every
    other board's fresh data. A board that scrapes 0 jobs legitimately is NOT
    tripped (only a source failure trips). No persistent cross-run state — every
    run re-attempts every board.

    Trip boundary — only these are treated as a source failure (caught):
      * ``httpx.HTTPError``: transport/HTTP failures (403/5xx via
        ``raise_for_status``, timeouts, connect errors, redirect loops) that
        escape the scrapers uncaught.
      * ``curl_cffi.requests.exceptions.RequestException`` (imported as
        ``CurlRequestException``): transport/HTTP failures raised by boards
        using the curl_cffi transport (hiringcafe since the Chrome-impersonation
        swap). Its hierarchy is ``RequestException -> CurlError -> OSError`` —
        it does NOT derive from ``httpx.HTTPError``, so it needs its own arm.
      * ``RuntimeError``: the deliberate signal scrapers raise for bot-block /
        site-structure changes (``No buildId``, ``Non-JSON response``,
        ``No pageProps``, geocoding failure).

    Any other exception (``TypeError``/``KeyError``/``AttributeError``/
    ``IndexError``/``ValueError``/``JSONDecodeError`` from scraper or
    normalization logic) is a genuine code defect and is re-raised to fail the
    run loudly — the same property that surfaces scraper/code regressions (see
    the ranked_csv + config-constant bugs, both caught because they failed).
    A defect must never be silently masked as a "tripped" no-op.
    """

    def deco(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(context: AssetExecutionContext) -> dg.MaterializeResult:
            try:
                return fn(context)
            except (httpx.HTTPError, CurlRequestException, RuntimeError) as exc:
                msg = f"{type(exc).__name__}: {exc}"
                context.log.error(
                    f"CIRCUIT TRIP [{board}]: {msg} — skipping {board} this run; "
                    "other sources continue."
                )
                append_bronze_trip(context.run_id, board, msg)
                return dg.MaterializeResult(
                    metadata={
                        "tripped": True,
                        "board": board,
                        "error": str(exc)[:300],
                    }
                )

        return wrapper

    return deco


@dg.asset(
    group_name="sources",
    description="Raw job listings scraped from free-work.com (Paris tech/IT)",
)
@trip_guard("freework")
def freework_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape free-work.com and normalize to canonical format."""
    from job_search_toolkit.scrapers.freework import (
        DEFAULT_CONTRACTS, DEFAULT_EXPERIENCE, DEFAULT_LOCATIONS,
        DEFAULT_QUERY, DEFAULT_REMOTE, DEFAULT_SORT,
        build_url, scrape,
    )
    from ..adapt_freework import normalize_freework_job

    ensure_data_dirs()
    rc = get_run_config()
    list_url = build_url(
        DEFAULT_QUERY, DEFAULT_LOCATIONS, DEFAULT_CONTRACTS,
        DEFAULT_REMOTE, DEFAULT_EXPERIENCE, DEFAULT_SORT, rc.freework_radius,
    )
    scrape(list_url, FREEWORK_RAW, max_pages=rc.max_pages, fmt="json")
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
@trip_guard("hiringcafe")
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
@trip_guard("hellowork")
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
@trip_guard("englishjobs")
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
@trip_guard("faruse")
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
@trip_guard("wwr")
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
@trip_guard("remoteok")
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
@trip_guard("datasciencejobs")
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
@trip_guard("linkedin_jobs")
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
@trip_guard("linkedin_posts")
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


@dg.asset(
    group_name="sources",
    description="Welcome to the Jungle France job offers via the deployed Apify actor (opt-in)",
)
@trip_guard("wttj")
def wttj_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape WTTJ France offers via Apify and normalize to canonical format."""
    from ..adapt_wttj import normalize_wttj_job

    if not os.getenv("APIFY_API_TOKEN"):
        context.log.warning(
            "APIFY_API_TOKEN is not set; writing empty wttj snapshot."
        )
        _write_bronze_snapshot("wttj", context.run_id, [])
        return dg.MaterializeResult(metadata={"total": 0})

    from apify_client import ApifyClient

    ensure_data_dirs()
    rc = get_run_config()
    client = ApifyClient(os.environ["APIFY_API_TOKEN"])
    run = client.actor("xSJbryo1TaOba9s9T").call(run_input={"maxItems": rc.wttj_max_jobs})
    if run is None:
        raise RuntimeError("WTTJ Apify actor call returned no run")
    raw = client.dataset(run.default_dataset_id).list_items().items
    WTTJ_RAW.write_text(json.dumps(raw), encoding="utf-8")
    canonical = [normalize_wttj_job(j) for j in raw]
    skipped = len(raw) - len(canonical)
    if skipped:
        context.log.warning(f"wttj: skipped {skipped} of {len(raw)} records that failed normalization (e.g. missing title).")
    else:
        context.log.info(f"wttj: normalized all {len(raw)} records.")
    _write_bronze_snapshot("wttj", context.run_id, canonical)
    return dg.MaterializeResult(metadata={"total": len(canonical)})


@dg.asset(
    group_name="sources",
    description="Built In France tech job listings (opt-in)",
)
@trip_guard("builtin")
def builtin_jobs(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Scrape builtin.com/jobs/eu/france and normalize to canonical format."""
    from job_search_toolkit.scrapers.builtin import scrape
    from ..adapt_builtin import normalize_builtin_job

    ensure_data_dirs()
    rc = get_run_config()
    scrape(BUILTIN_RAW, max_pages=rc.builtin_max_pages, fmt="json")
    raw = json.loads(BUILTIN_RAW.read_text(encoding="utf-8")) if BUILTIN_RAW.exists() else []
    canonical = [normalize_builtin_job(j) for j in raw]
    skipped = len(raw) - len(canonical)
    if skipped:
        context.log.warning(f"builtin: skipped {skipped} of {len(raw)} records that failed normalization (e.g. missing title).")
    else:
        context.log.info(f"builtin: normalized all {len(raw)} records.")
    _write_bronze_snapshot("builtin", context.run_id, canonical)
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
    "wttj": wttj_jobs,
    "builtin": builtin_jobs,
}
