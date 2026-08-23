"""Tests for the LinkedIn scrape assets and the ``kinds`` adapter filter.

Covers the two LinkedIn boards:

- ``linkedin_jobs`` — runs ``run_discovery(kinds=["job"])`` and normalizes
  ``JobRecord``s into ``linkedin_jobs`` bronze rows.
- ``linkedin_posts`` — runs ``run_discovery(kinds=["post"])``, drops posts
  whose regex verdict is ``drop``, and normalizes the rest into
  ``linkedin_posts`` bronze rows.

Plus the graceful no-discovery-key path (empty bronze snapshot, no raise).
"""

from __future__ import annotations

from pathlib import Path

import dagster as dg
import httpx

import job_search_toolkit.linkedin.adapter as adapter_module
import job_search_toolkit.pipelines.jd.assets.scrape as scrape_module
from job_search_toolkit.linkedin.adapter import DiscoveryOutcome, run_discovery
from job_search_toolkit.linkedin.config import LinkedInConfig
from job_search_toolkit.linkedin.discovery import DiscoveryRun, SearchResult
from job_search_toolkit.linkedin.models import JobRecord, PostRecord
from job_search_toolkit.linkedin.tech_scan import TechnologyScanner

FIXTURES = Path(__file__).parent / "fixtures"

POST_URL = "https://www.linkedin.com/posts/vcreatek_hiring-microsoftfabric-powerapps-activity-7488545218842"
JOB_URL = "https://fr.linkedin.com/jobs/view/analytics-engineer-microsoft-fabric-cdi-lille-at-logical-conseils-4436738979"

_DISCOVERY_KEYS = ("APIFY_TOKEN", "APIFY_API_TOKEN", "TAVILY_API_KEY")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class StubBackend:
    """Deterministic discovery backend returning a fixed result set."""

    name = "stub"

    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results

    def search(self, queries, *, country_code=None, language_code=None) -> DiscoveryRun:
        return DiscoveryRun(
            backend=self.name,
            results=list(self._results),
            cost_usd=0.0185,
            usage={"n_queries": len(queries)},
        )


def _mock_client() -> httpx.Client:
    post_html = (FIXTURES / "post_page.html").read_text(encoding="utf-8")
    job_html = (FIXTURES / "job_page.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == POST_URL:
            return httpx.Response(200, text=post_html)
        if str(request.url) == JOB_URL:
            return httpx.Response(200, text=job_html)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _config() -> LinkedInConfig:
    return LinkedInConfig(backend="apify", post_queries=("q1",), job_queries=("q2",))


def make_job(**overrides: object) -> JobRecord:
    """Return a realistic full ``JobRecord`` with overrides applied."""
    base: JobRecord = {
        "job_url": JOB_URL,
        "job_id": "4436738979",
        "title": "Senior Data Engineer",
        "company": "Acme Corp",
        "company_url": "https://acme.example.com",
        "location": {"country": "FR", "locality": "Paris"},
        "employment_type": "FULL_TIME",
        "date_posted": "2026-08-01",
        "description": "Build and scale the data platform.",
        "technologies": ["Python", "Spark"],
        "content_quality": "full",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def make_post(text: str, **overrides: object) -> PostRecord:
    """Return a ``PostRecord`` carrying ``text`` (plus overrides)."""
    base: PostRecord = {
        "post_url": "https://www.linkedin.com/posts/alice_hi-activity-7488545218842",
        "author_name": "Alice Recruiter",
        "author_profile_url": "https://www.linkedin.com/in/alice",
        "date_published": "2026-08-02",
        "text": text,
        "technologies": ["Python", "Spark"],
        "likes": 12,
        "activity_id": "7488545218842",
        "name_from_slug": False,
        "content_quality": "full",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _context() -> dg.OpExecutionContext:
    return dg.build_op_context()


# ---------------------------------------------------------------------------
# run_discovery kinds filter
# ---------------------------------------------------------------------------


def test_run_discovery_kinds_job_runs_only_job_pass():
    backend = StubBackend(
        [
            SearchResult(url=POST_URL, title="post", snippet=""),
            SearchResult(url=JOB_URL, title="job", snippet=""),
        ]
    )
    outcome = run_discovery(
        _config(),
        backend=backend,
        client=_mock_client(),
        scanner=TechnologyScanner.from_defaults(),
        kinds=["job"],
    )

    assert outcome.jobs, "job pass should produce jobs"
    assert outcome.jobs[0]["job_id"] == "4436738979"
    assert outcome.posts == []
    assert "jobs" in outcome.usage
    assert "posts" not in outcome.usage


def test_run_discovery_kinds_post_runs_only_post_pass():
    backend = StubBackend(
        [
            SearchResult(url=POST_URL, title="post", snippet=""),
            SearchResult(url=JOB_URL, title="job", snippet=""),
        ]
    )
    outcome = run_discovery(
        _config(),
        backend=backend,
        client=_mock_client(),
        scanner=TechnologyScanner.from_defaults(),
        kinds=["post"],
    )

    assert outcome.posts, "post pass should produce posts"
    assert outcome.posts[0]["activity_id"] == "7488545218842"
    assert outcome.jobs == []
    assert "posts" in outcome.usage
    assert "jobs" not in outcome.usage


# ---------------------------------------------------------------------------
# Graceful skip without a discovery key
# ---------------------------------------------------------------------------


def test_asset_without_discovery_key_writes_empty_snapshot(monkeypatch):
    for key in _DISCOVERY_KEYS:
        monkeypatch.delenv(key, raising=False)

    written: list[tuple] = []
    monkeypatch.setattr(
        scrape_module, "_write_bronze_snapshot", lambda board, run_id, jobs: written.append((board, run_id, jobs))
    )

    ctx = _context()
    result = scrape_module.linkedin_jobs(ctx)

    assert written == [("linkedin_jobs", ctx.run_id, [])]
    assert result.metadata["total"] == 0


def test_posts_asset_without_discovery_key_writes_empty_snapshot(monkeypatch):
    for key in _DISCOVERY_KEYS:
        monkeypatch.delenv(key, raising=False)

    written: list[tuple] = []
    monkeypatch.setattr(
        scrape_module, "_write_bronze_snapshot", lambda board, run_id, jobs: written.append((board, run_id, jobs))
    )

    ctx = _context()
    result = scrape_module.linkedin_posts(ctx)

    assert written == [("linkedin_posts", ctx.run_id, [])]
    assert result.metadata["total"] == 0


# ---------------------------------------------------------------------------
# Fixture flows through the normalizer into the bronze list
# ---------------------------------------------------------------------------


def test_linkedin_jobs_asset_normalizes_job_into_bronze(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")

    written: list[tuple] = []
    monkeypatch.setattr(
        scrape_module, "_write_bronze_snapshot", lambda board, run_id, jobs: written.append((board, run_id, jobs))
    )
    monkeypatch.setattr(
        adapter_module, "run_discovery", lambda config, kinds=None: DiscoveryOutcome(jobs=[make_job()])
    )

    ctx = _context()
    result = scrape_module.linkedin_jobs(ctx)

    board, run_id, canonical = written[0]
    assert board == "linkedin_jobs"
    assert run_id == ctx.run_id
    assert len(canonical) == 1
    assert canonical[0]["source_board"] == "linkedin_jobs"
    assert canonical[0]["title"] == "Senior Data Engineer"
    assert canonical[0]["company"] == "Acme Corp"
    assert result.metadata["total"] == 1


def test_linkedin_posts_asset_normalizes_post_into_bronze(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    written: list[tuple] = []
    monkeypatch.setattr(
        scrape_module, "_write_bronze_snapshot", lambda board, run_id, jobs: written.append((board, run_id, jobs))
    )
    monkeypatch.setattr(
        adapter_module,
        "run_discovery",
        lambda config, kinds=None: DiscoveryOutcome(posts=[make_post("Hiring a Senior Data Engineer in Paris.")]),
    )

    ctx = _context()
    result = scrape_module.linkedin_posts(ctx)

    board, run_id, canonical = written[0]
    assert board == "linkedin_posts"
    assert run_id == ctx.run_id
    assert len(canonical) == 1
    assert canonical[0]["source_board"] == "linkedin_posts"
    assert "Senior" in canonical[0]["title"] and "Data Engineer" in canonical[0]["title"]
    assert result.metadata["total"] == 1


def test_linkedin_posts_asset_filters_dropped_posts(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")

    dropped = make_post("Happy Friday everyone, enjoy the weekend!")  # verdict: drop
    landed = make_post("Hiring a Senior Data Engineer in Paris.")     # verdict: land
    written: list[tuple] = []
    monkeypatch.setattr(
        scrape_module, "_write_bronze_snapshot", lambda board, run_id, jobs: written.append((board, run_id, jobs))
    )
    monkeypatch.setattr(
        adapter_module,
        "run_discovery",
        lambda config, kinds=None: DiscoveryOutcome(posts=[dropped, landed]),
    )

    ctx = _context()
    result = scrape_module.linkedin_posts(ctx)

    board, run_id, canonical = written[0]
    assert board == "linkedin_posts"
    assert run_id == ctx.run_id
    assert len(canonical) == 1
    assert canonical[0]["source_board"] == "linkedin_posts"
    assert result.metadata["total"] == 1
