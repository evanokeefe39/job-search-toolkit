"""LinkedIn source adapter orchestration.

Wires the pipeline from the build spec together:

    discover → filter → fetch → parse → dedup → tech scan → candidate pool

The candidate pool is the human gate: records land as JSON/CSV under
``data/linkedin/`` (gitignored). Nothing here creates a Twenty Opportunity or
touches the silver warehouse — shortlisting is a human decision.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

import httpx

from job_search_toolkit.linkedin.config import LinkedInConfig
from job_search_toolkit.linkedin.discovery import (
    DiscoveryBackend,
    LinkedInGuestBackend,
    SearchResult,
    discover,
    make_backend,
)
from job_search_toolkit.linkedin.fetch import FetchError, fetch_page
from job_search_toolkit.linkedin.models import JobRecord, PostRecord
from job_search_toolkit.linkedin.parse import parse_job, parse_post
from job_search_toolkit.linkedin.tech_scan import TechnologyScanner
from job_search_toolkit.linkedin.urls import classify_url

_GONE = frozenset({404, 410})

DEFAULT_OUT_DIR = Path("data/linkedin")


@dataclass
class DiscoveryOutcome:
    """Everything one adapter run learned, before any human gate."""

    posts: list[PostRecord] = field(default_factory=list)
    jobs: list[JobRecord] = field(default_factory=list)
    stale_urls: list[str] = field(default_factory=list)   # fetched → 404/410
    failed_urls: list[str] = field(default_factory=list)  # other fetch errors
    cost_usd: float | None = None
    usage: dict[str, object] = field(default_factory=dict)


def _make_scanner(config: LinkedInConfig) -> TechnologyScanner:
    """Build the tech scanner from the configured keyword file or defaults."""
    if config.technology_list:
        path = Path(config.technology_list)
        if path.exists():
            return TechnologyScanner.from_file(path)
    return TechnologyScanner.from_defaults()


def _filter(results: list[SearchResult], kind: str) -> list[SearchResult]:
    """Keep only results whose URL shape matches ``kind`` (``post`` | ``job``)."""
    return [r for r in results if classify_url(r["url"]) == kind]


def _dedup_urls(results: list[SearchResult]) -> list[SearchResult]:
    """Drop duplicate URLs returned across multiple search queries."""
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out


def _dedup_posts(posts: list[PostRecord]) -> list[PostRecord]:
    """Dedup posts by activity id, falling back to author profile URL."""
    seen: set[str] = set()
    out: list[PostRecord] = []
    for p in posts:
        key = p["activity_id"] or p["author_profile_url"] or p["post_url"]
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _dedup_jobs(jobs: list[JobRecord]) -> list[JobRecord]:
    """Dedup jobs by trailing job id, falling back to the job URL."""
    seen: set[str] = set()
    out: list[JobRecord] = []
    for j in jobs:
        key = j["job_id"] or j["job_url"]
        if key not in seen:
            seen.add(key)
            out.append(j)
    return out


# Recognized French cities (lowercase, no accents). Partial/login-wall job
# records carry ``country is None``; a locality naming one of these (optionally
# with a "et périphérie" / "et ses environs" suffix) keeps the job France.
FRENCH_LOCALITIES = frozenset(
    {
        "paris",
        "lyon",
        "lille",
        "marseille",
        "bordeaux",
        "toulouse",
        "nantes",
        "strasbourg",
        "rennes",
        "montpellier",
    }
)


def _is_france_job(job: JobRecord, country_code: str = "fr") -> bool:
    """Return True when ``job`` is confirmed to be located in France.

    A job is kept when its ``location.country`` matches the configured
    ``country_code`` (case-insensitive), or — for partial/login-wall records
    whose country is unknown — when its ``location.locality`` names a
    recognized French city. Returns False for a confirmed non-FR country and
    for unknown-country jobs with no French locality. Posts are never passed
    through this filter (they carry no location).
    """
    country = (job["location"]["country"] or "").strip()
    if country:
        return country.upper() == country_code.upper()

    locality = (job["location"]["locality"] or "").strip().lower()
    if not locality:
        return False
    return any(
        locality == city or locality.startswith(city + " ") for city in FRENCH_LOCALITIES
    )


def _run_pass(
    queries: tuple[str, ...],
    kind: str,
    config: LinkedInConfig,
    backend: DiscoveryBackend,
    scanner: TechnologyScanner,
    client: httpx.Client | None,
) -> tuple[list[PostRecord] | list[JobRecord], list[str], list[str], float | None, dict[str, object]]:
    """Discover + filter + fetch + parse + tech-scan one artifact kind."""
    records: list[PostRecord] | list[JobRecord] = []
    stale: list[str] = []
    failed: list[str] = []

    run = discover(
        list(queries),
        backend,
        country_code=config.country_code,
        language_code=config.language_code,
    )
    urls = _dedup_urls(_filter(run["results"], kind))
    for result in urls:
        url = result["url"]
        try:
            html = fetch_page(url, client=client)
        except FetchError as exc:
            if exc.status_code in _GONE:
                stale.append(url)
            else:
                failed.append(url)
            continue
        if kind == "post":
            rec: PostRecord | JobRecord = parse_post(html, url)
            rec["technologies"] = scanner.scan(rec["text"])
        else:
            rec = parse_job(html, url)
            if not _is_france_job(rec, config.country_code):
                continue
            rec["technologies"] = scanner.scan(rec["description"])
        records.append(rec)

    return records, stale, failed, run["cost_usd"], run["usage"]


def run_discovery(
    config: LinkedInConfig,
    backend: DiscoveryBackend | None = None,
    *,
    client: httpx.Client | None = None,
    scanner: TechnologyScanner | None = None,
    kinds: Sequence[Literal["post", "job"]] = ("post", "job"),
) -> DiscoveryOutcome:
    """Run the adapter: discover, fetch, parse, dedup, and tech-scan.

    ``kinds`` selects which artifact kinds to run; only the requested passes
    (``"post"`` and/or ``"job"``) are executed. ``backend``/``client``/
    ``scanner`` may be injected for tests; when omitted they are built from
    ``config`` and the environment.
    """
    # Posts are recruiter posts (no structured job search); they always use the
    # configured search backend (apify/tavily). Jobs use the free LinkedIn guest
    # API by default (``guest_jobs``) — unless a backend was injected (test
    # seam), in which case both kinds use it. See docs/linkedin-source-spike.md.
    post_backend = backend or make_backend(config.backend)
    job_backend = (
        LinkedInGuestBackend() if (config.guest_jobs and backend is None) else post_backend
    )
    scanner = scanner or _make_scanner(config)

    outcome = DiscoveryOutcome()
    if "post" in kinds and config.post_queries:
        posts, stale, failed, cost, usage = _run_pass(
            config.post_queries, "post", config, post_backend, scanner, client
        )
        outcome.posts = _dedup_posts(posts)
        outcome.stale_urls.extend(stale)
        outcome.failed_urls.extend(failed)
        outcome.usage["posts"] = usage
        if cost is not None:
            outcome.cost_usd = cost

    if "job" in kinds and config.job_queries:
        jobs, stale, failed, cost, usage = _run_pass(
            config.job_queries, "job", config, job_backend, scanner, client
        )
        outcome.jobs = _dedup_jobs(jobs)
        outcome.stale_urls.extend(stale)
        outcome.failed_urls.extend(failed)
        outcome.usage["jobs"] = usage
        if cost is not None:
            outcome.cost_usd = (outcome.cost_usd or 0.0) + cost

    return outcome


_POST_FIELDS = [
    "author_name",
    "author_profile_url",
    "post_url",
    "activity_id",
    "date_published",
    "likes",
    "technologies",
    "content_quality",
    "text",
]

_JOB_FIELDS = [
    "job_url",
    "job_id",
    "title",
    "company",
    "company_url",
    "country",
    "locality",
    "employment_type",
    "date_posted",
    "technologies",
    "description",
]


def _flatten_job(job: JobRecord) -> dict[str, object]:
    row = {k: job[k] for k in ["job_url", "job_id", "title", "company", "company_url", "employment_type", "date_posted", "description"]}  # type: ignore[misc]
    row["country"] = job["location"]["country"]
    row["locality"] = job["location"]["locality"]
    row["technologies"] = "|".join(job["technologies"])
    return row


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_candidate_pool(
    outcome: DiscoveryOutcome, out_dir: str | Path = DEFAULT_OUT_DIR
) -> dict[str, Path]:
    """Persist the candidate pool to ``out_dir`` (JSON + CSV), gitignored.

    No Twenty/CRM writes happen here — this is the human gate.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    posts_json = out / "posts.json"
    jobs_json = out / "jobs.json"
    posts_json.write_text(
        json.dumps(outcome.posts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    jobs_json.write_text(
        json.dumps(outcome.jobs, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    post_rows = [{**p, "technologies": "|".join(p["technologies"])} for p in outcome.posts]
    _write_csv(out / "posts.csv", _POST_FIELDS, post_rows)
    _write_csv(out / "jobs.csv", _JOB_FIELDS, [_flatten_job(j) for j in outcome.jobs])

    return {"posts": posts_json, "jobs": jobs_json, "posts_csv": out / "posts.csv", "jobs_csv": out / "jobs.csv"}
