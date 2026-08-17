"""Tests for the LinkedIn fetch/url/parse modules.

Fixtures: ``tests/fixtures/post_page.html`` and ``tests/fixtures/job_page.html``
(loaded via ``Path(__file__).parent / "fixtures"``).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from job_search_toolkit.linkedin.fetch import FetchError, fetch_page
from job_search_toolkit.linkedin.parse import extract_jsonld, parse_job, parse_post
from job_search_toolkit.linkedin.urls import (
    author_slug_from_post_url,
    classify_url,
    job_id_from_url,
    normalize_host,
    post_activity_id,
)

_FIXTURES = Path(__file__).parent / "fixtures"
POST_HTML = (_FIXTURES / "post_page.html").read_text(encoding="utf-8")
JOB_HTML = (_FIXTURES / "job_page.html").read_text(encoding="utf-8")

JOB_URL = (
    "https://fr.linkedin.com/jobs/view/analytics-engineer-microsoft-fabric-cdi-"
    "lille-at-logical-conseils-4436738979"
)
POST_URL = "https://www.linkedin.com/posts/vcreatek_hiring-microsoftfabric-powerapps-activity-7488545218842"


# --- 1. parse_job on the real fixture ---------------------------------------


def test_parse_job_full():
    rec = parse_job(JOB_HTML, JOB_URL)
    assert "Analytics Engineer" in rec["title"]
    assert rec["company"] == "Logical Conseils"
    assert rec["company_url"] is not None
    assert "logical-conseils" in rec["company_url"]
    assert rec["location"]["country"] == "FR"
    assert rec["location"]["locality"] == "Lille et périphérie"
    assert rec["employment_type"] == "FULL_TIME"
    assert rec["date_posted"] is not None
    assert rec["date_posted"].startswith("2026-07-15")
    assert "Microsoft Fabric" in rec["description"]
    assert "<p>" not in rec["description"]
    assert "&lt;" not in rec["description"]
    assert rec["job_id"] == "4436738979"
    assert rec["content_quality"] == "full"


# --- 2. parse_post on the real fixture --------------------------------------


def test_parse_post_full():
    rec = parse_post(POST_HTML, POST_URL)
    assert rec["author_name"] == "Jane Recruiter"
    assert rec["author_profile_url"] is not None
    assert rec["author_profile_url"].endswith("/in/jane-recruiter-123")
    assert rec["date_published"] is not None
    assert rec["date_published"].startswith("2026-06-04")
    assert rec["likes"] == 10
    assert "Microsoft Fabric" in rec["text"]
    assert rec["activity_id"] == "7488545218842"
    assert rec["name_from_slug"] is False
    assert rec["content_quality"] == "full"


# --- 3. parse_post slug fallback --------------------------------------------


def test_parse_post_slug_fallback():
    html = (
        '<html><head><meta property="og:description" content="hello"/></head>'
        "<body></body></html>"
    )
    url = "https://www.linkedin.com/posts/jane-doe-123abc_some-slug-activity-999"
    rec = parse_post(html, url)
    assert rec["name_from_slug"] is True
    assert rec["author_profile_url"] is not None
    assert rec["author_profile_url"].endswith("/in/jane-doe-123abc")
    assert rec["text"] == "hello"
    assert rec["content_quality"] == "partial"
    assert rec["activity_id"] == "999"
    assert rec["likes"] is None


# --- 4. classify_url --------------------------------------------------------


def test_classify_url():
    assert classify_url("https://www.linkedin.com/posts/vcreatek_hi-activity-1") == "post"
    assert classify_url("https://fr.linkedin.com/jobs/view/foo-123") == "job"
    assert classify_url("https://www.linkedin.com/jobs/search/") == "drop"
    assert classify_url("https://www.linkedin.com/jobs/microsoft-fabric-jobs") == "drop"
    assert classify_url("https://example.com/posts/x") == "drop"
    assert classify_url("https://mu.linkedin.com/jobs/foo") == "drop"
    assert classify_url("https://fr.linkedin.com/jobs/view/x") == "job"


# --- 5. pure URL helpers ----------------------------------------------------


def test_url_helpers():
    # post_activity_id: -activity-<digits> and urn:li:activity:<digits>
    assert post_activity_id("https://www.linkedin.com/posts/a_b-activity-7488545218842") == "7488545218842"
    assert post_activity_id("https://www.linkedin.com/feed/update/urn:li:activity:7488545218842") == "7488545218842"
    assert post_activity_id("https://www.linkedin.com/posts/a_b_no-activity-here") is None
    # job_id_from_url: trailing digits after the last "-"
    assert job_id_from_url("https://fr.linkedin.com/jobs/view/analytics-engineer-foo-4436738979") == "4436738979"
    assert job_id_from_url("https://fr.linkedin.com/jobs/view/analytics-engineer-foo") is None
    assert job_id_from_url("https://fr.linkedin.com/jobs/view/foo-12a") is None
    # author_slug_from_post_url: segment between /posts/ and first "_"
    assert author_slug_from_post_url("https://www.linkedin.com/posts/vcreatek_hiring-x-activity-1") == "vcreatek"
    assert author_slug_from_post_url("https://www.linkedin.com/jobs/view/x") is None
    # normalize_host
    assert normalize_host("https://fr.linkedin.com/jobs/view/x") == "linkedin.com"
    assert normalize_host("https://www.linkedin.com/posts/x") == "linkedin.com"
    assert normalize_host("https://WWW.LinkedIn.COM/posts/x") == "linkedin.com"
    assert normalize_host("https://www.example.com/posts/x") == "example.com"
    assert normalize_host("https://sub.example.org/x") == "sub.example.org"


# --- 6. extract_jsonld skips malformed scripts ------------------------------


def test_extract_jsonld_skips_malformed():
    html = (
        '<script type="application/ld+json">{"@type":"JobPosting","title":"Good"}</script>'
        '<script type="application/ld+json">this is not json</script>'
        '<script type="application/ld+json">{"@type":"SocialMediaPosting"}</script>'
    )
    blocks = extract_jsonld(html)
    assert len(blocks) == 2
    assert blocks[0]["@type"] == "JobPosting"
    assert blocks[1]["@type"] == "SocialMediaPosting"


def test_find_jsonld_graph_entry():
    html = (
        '<script type="application/ld+json">{"@context":"https://schema.org","@graph":['
        '{"@type":"WebPage","url":"https://x"},'
        '{"@type":"JobPosting","title":"In Graph","datePosted":"2026-01-01T00:00:00Z"}]}</script>'
    )
    rec = parse_job(html, "https://www.linkedin.com/jobs/view/graph-job-77")
    assert rec["title"] == "In Graph"
    assert rec["content_quality"] == "full"
    assert rec["job_id"] == "77"


# --- fallback variants ------------------------------------------------------


def test_parse_job_fallback():
    html = (
        '<html><head><meta property="og:title" content="Data Engineer CDI"/>'
        '<meta property="og:description" content="We hire"/>'
        "</head><body></body></html>"
    )
    rec = parse_job(html, "https://www.linkedin.com/jobs/view/data-engineer-42")
    assert rec["title"] == "Data Engineer CDI"
    assert rec["company"] == ""
    assert rec["company_url"] is None
    assert rec["location"] == {"country": None, "locality": None}
    assert rec["description"] == "We hire"
    assert rec["date_posted"] is None
    assert rec["employment_type"] is None
    assert rec["job_id"] == "42"
    assert rec["content_quality"] == "partial"


def test_parse_post_no_slug():
    html = '<html><head><meta property="og:description" content="hi"/></head><body></body></html>'
    rec = parse_post(html, "https://www.linkedin.com/feed/update/urn:li:activity:555")
    assert rec["author_name"] == ""
    assert rec["author_profile_url"] is None
    assert rec["name_from_slug"] is False
    assert rec["activity_id"] == "555"
    assert rec["content_quality"] == "partial"


# --- 7/8. fetch_page --------------------------------------------------------


def test_fetch_404_no_retry():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError) as exc_info:
        fetch_page("https://www.linkedin.com/jobs/view/x", client=client)
    assert exc_info.value.status_code == 404
    assert len(calls) == 1


def test_fetch_retries_then_succeeds():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500)
        return httpx.Response(200, text="second attempt body")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    body = fetch_page(
        "https://www.linkedin.com/jobs/view/x",
        client=client,
        retries=2,
        backoff=0.01,
    )
    assert body == "second attempt body"
    assert len(calls) == 2


def test_fetch_retries_exhausted():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError) as exc_info:
        fetch_page(
            "https://www.linkedin.com/jobs/view/x",
            client=client,
            retries=2,
            backoff=0.01,
        )
    assert exc_info.value.status_code == 500
    assert len(calls) == 3  # initial try + 2 retries


def test_fetch_other_4xx_no_retry():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError) as exc_info:
        fetch_page("https://www.linkedin.com/jobs/view/x", client=client)
    assert exc_info.value.status_code == 403
    assert len(calls) == 1
