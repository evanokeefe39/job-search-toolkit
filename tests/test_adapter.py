"""End-to-end tests for the LinkedIn adapter orchestration + candidate pool."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from job_search_toolkit.linkedin.adapter import (
    FRENCH_LOCALITIES,
    DiscoveryOutcome,
    _dedup_jobs,
    _dedup_posts,
    _filter,
    _is_france_job,
    run_discovery,
    write_candidate_pool,
)
from job_search_toolkit.linkedin.config import LinkedInConfig
from job_search_toolkit.linkedin.discovery import DiscoveryRun, SearchResult
from job_search_toolkit.linkedin.tech_scan import TechnologyScanner

FIXTURES = Path(__file__).parent / "fixtures"

POST_URL = "https://www.linkedin.com/posts/vcreatek_hiring-microsoftfabric-powerapps-activity-7488545218842"
JOB_URL = "https://fr.linkedin.com/jobs/view/analytics-engineer-microsoft-fabric-cdi-lille-at-logical-conseils-4436738979"
STALE_URL = "https://www.linkedin.com/posts/expired_post_activity-1111111111111"


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


def _post(activity_id, profile, url):
    return {
        "post_url": url,
        "author_name": "x",
        "author_profile_url": profile,
        "date_published": None,
        "text": "",
        "technologies": [],
        "likes": None,
        "activity_id": activity_id,
        "name_from_slug": False,
        "content_quality": "full",
    }


def _job(job_id, url):
    return {
        "job_url": url,
        "job_id": job_id,
        "title": "",
        "company": "",
        "company_url": None,
        "location": {"country": None, "locality": None},
        "employment_type": None,
        "date_posted": None,
        "description": "",
        "technologies": [],
        "content_quality": "full",
    }


def test_run_discovery_end_to_end():
    backend = StubBackend(
        [
            SearchResult(url=POST_URL, title="post", snippet=""),
            SearchResult(url=JOB_URL, title="job", snippet=""),
            SearchResult(url=STALE_URL, title="stale", snippet=""),
            SearchResult(url="https://www.linkedin.com/jobs/search/?q=x", title="index", snippet=""),
            SearchResult(url="https://example.com/posts/x", title="external", snippet=""),
        ]
    )
    outcome = run_discovery(
        _config(),
        backend=backend,
        client=_mock_client(),
        scanner=TechnologyScanner.from_defaults(),
    )

    assert len(outcome.posts) == 1
    post = outcome.posts[0]
    assert post["author_name"] == "Jane Recruiter"
    assert post["activity_id"] == "7488545218842"
    assert "Fabric" in post["technologies"]

    assert len(outcome.jobs) == 1
    job = outcome.jobs[0]
    assert job["company"] == "Logical Conseils"
    assert job["job_id"] == "4436738979"
    assert "Fabric" in job["technologies"]

    assert STALE_URL in outcome.stale_urls
    assert outcome.cost_usd is not None and outcome.cost_usd >= 0.0185


def test_filter_keeps_only_matching_shape():
    results = [
        SearchResult(url=POST_URL, title="", snippet=""),
        SearchResult(url="https://www.linkedin.com/jobs/search/?q=x", title="", snippet=""),
        SearchResult(url="https://example.com/posts/x", title="", snippet=""),
        SearchResult(url=JOB_URL, title="", snippet=""),
    ]
    assert len(_filter(results, "post")) == 1
    assert len(_filter(results, "job")) == 1


def test_dedup_posts_by_activity_then_profile():
    records = [
        _post("1", "https://www.linkedin.com/in/a", "u1"),
        _post("1", "https://www.linkedin.com/in/a", "u2"),
        _post(None, "https://www.linkedin.com/in/c", "u3"),
        _post(None, "https://www.linkedin.com/in/c", "u4"),
    ]
    assert len(_dedup_posts(records)) == 2


def test_dedup_jobs_by_job_id():
    records = [_job("1", "u1"), _job("1", "u2"), _job("2", "u3")]
    assert len(_dedup_jobs(records)) == 2


def test_write_candidate_pool(tmp_path):
    outcome = DiscoveryOutcome(posts=[_post("1", "https://www.linkedin.com/in/a", "u1")], jobs=[_job("1", "u1")])
    paths = write_candidate_pool(outcome, tmp_path)

    assert (tmp_path / "posts.json").exists()
    assert (tmp_path / "posts.csv").exists()
    assert (tmp_path / "jobs.json").exists()
    assert (tmp_path / "jobs.csv").exists()

    data = json.loads((tmp_path / "posts.json").read_text(encoding="utf-8"))
    assert data[0]["activity_id"] == "1"
    assert set(paths) == {"posts", "jobs", "posts_csv", "jobs_csv"}
    assert paths["jobs"].suffix == ".json"


# ---------------------------------------------------------------------------
# Deterministic France filter
# ---------------------------------------------------------------------------


def _job_with_location(country, locality):
    job = _job("1", "u1")
    job["location"] = {"country": country, "locality": locality}
    return job


def test_france_filter_keeps_fr_country():
    assert _is_france_job(_job_with_location("FR", "Paris"))
    # country_code comparison is case-insensitive
    assert _is_france_job(_job_with_location("fr", "Lyon"))


def test_france_filter_drops_non_fr_country():
    for cc, locality in (("AU", "Sydney"), ("IN", "Mumbai"), ("US", "Atlanta")):
        assert _is_france_job(_job_with_location(cc, locality)) is False


def test_france_filter_drops_unknown_country_without_french_locality():
    assert _is_france_job(_job_with_location(None, "Atlanta")) is False
    assert _is_france_job(_job_with_location(None, None)) is False
    assert _is_france_job(_job_with_location(None, "")) is False


def test_france_filter_keeps_unknown_country_with_french_locality():
    assert _is_france_job(_job_with_location(None, "Paris et périphérie"))
    assert _is_france_job(_job_with_location(None, "Lille et périphérie"))
    assert _is_france_job(_job_with_location(None, "  Montpellier  "))


def test_france_filter_respects_country_code_param():
    assert _is_france_job(_job_with_location("BE", "Brussels"), "be") is True
    assert _is_france_job(_job_with_location("FR", "Paris"), "be") is False


def test_french_localities_set_known_cities():
    assert FRENCH_LOCALITIES >= {
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


def test_posts_are_not_country_filtered():
    backend = StubBackend([SearchResult(url=POST_URL, title="post", snippet="")])
    outcome = run_discovery(
        _config(),
        backend=backend,
        client=_mock_client(),
        scanner=TechnologyScanner.from_defaults(),
        kinds=["post"],
    )
    assert len(outcome.posts) == 1
    assert outcome.posts[0]["activity_id"] == "7488545218842"


def test_config_country_code_defaults_to_fr():
    assert LinkedInConfig().country_code == "fr"
    assert LinkedInConfig.from_preferences("does-not-exist.yaml").country_code == "fr"
