"""Unit tests for linkedin.discovery — raw LinkedIn URL discovery.

Covers:
- flatten_apify_dataset / flatten_tavily_response against real fixtures
- ApifyBackend happy path, failed-run error, missing-token error
- TavilyBackend happy path
- make_backend dispatch

HTTP is mocked with httpx.MockTransport injected via the ``_open_client``
test seam. Run: uv run pytest tests/test_discovery.py
"""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from job_search_toolkit.linkedin import discovery as discovery_module
from job_search_toolkit.linkedin.discovery import (
    ApifyBackend,
    TavilyBackend,
    flatten_apify_dataset,
    flatten_tavily_response,
    make_backend,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_ACTOR = "apify~google-search-scraper"


def _load_fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Route every HTTP call the backends make through ``handler``."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        discovery_module, "_open_client", lambda: httpx.Client(transport=transport)
    )


def test_flatten_apify_dataset_contains_job_views() -> None:
    results = flatten_apify_dataset(_load_fixture("apify_dataset.json"))

    assert results
    for result in results:
        assert set(result.keys()) == {"url", "title", "snippet"}
    job_views = [r for r in results if "/jobs/view/" in r["url"]]
    assert job_views
    assert all(r["title"] and r["snippet"] for r in job_views)


def test_flatten_tavily_response_contains_job_views() -> None:
    results = flatten_tavily_response(_load_fixture("tavily_response.json"))

    assert len(results) >= 8
    for result in results:
        assert set(result.keys()) == {"url", "title", "snippet"}
        assert result["url"] and result["title"] and result["snippet"]
    assert any("/jobs/view/" in r["url"] for r in results)


def test_apify_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/v2/acts/{_ACTOR}/runs":
            assert request.method == "POST"
            assert json.loads(request.content)["queries"] == "q"
            return httpx.Response(200, json={"data": {"id": "r1"}})
        if path == "/v2/actor-runs/r1":
            return httpx.Response(
                200, json={"data": {"status": "SUCCEEDED", "usageTotalUsd": 0.0185}}
            )
        if path == "/v2/actor-runs/r1/dataset/items":
            return httpx.Response(200, json=_load_fixture("apify_dataset.json"))
        return httpx.Response(404, text=f"unexpected path {path}")

    _patch_client(monkeypatch, handler)
    backend = ApifyBackend(token="test", actor_id=_ACTOR, poll_interval=0.0)
    run = backend.search(["q"])

    assert run["backend"] == "apify"
    assert run["cost_usd"] == 0.0185
    assert run["results"]
    assert run["usage"]["run_id"] == "r1"
    assert run["usage"]["n_queries"] == 1


def test_apify_failed_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/v2/acts/{_ACTOR}/runs":
            return httpx.Response(200, json={"data": {"id": "r1"}})
        if path == "/v2/actor-runs/r1":
            return httpx.Response(
                200, json={"data": {"status": "FAILED", "usageTotalUsd": 0.004}}
            )
        return httpx.Response(404, text=f"unexpected path {path}")

    _patch_client(monkeypatch, handler)
    backend = ApifyBackend(token="test", actor_id=_ACTOR, poll_interval=0.0)

    with pytest.raises(RuntimeError, match="FAILED"):
        backend.search(["q"])


def test_tavily_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            assert request.method == "POST"
            body = json.loads(request.content)
            assert body["api_key"] == "test-key"
            assert body["include_domains"] == ["linkedin.com"]
            return httpx.Response(200, json=_load_fixture("tavily_response.json"))
        return httpx.Response(404, text=f"unexpected path {request.url.path}")

    _patch_client(monkeypatch, handler)
    backend = TavilyBackend(api_key="test-key")
    run = backend.search(["q"])

    assert run["backend"] == "tavily"
    assert run["cost_usd"] is None
    assert run["usage"]["n_queries"] == 1
    assert run["results"]
    assert run["usage"]["n_results"] == len(run["results"])


def test_make_backend_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="foo"):
        make_backend("foo")


def test_apify_backend_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError):
        ApifyBackend(token=None)


def test_apify_backend_uses_api_token_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.setenv("APIFY_API_TOKEN", "api-token-123")
    backend = ApifyBackend(token=None)
    assert backend.token == "api-token-123"


# --- LinkedInGuestBackend (public guest jobs API) ---

def test_parse_guest_query_variants():
    from job_search_toolkit.linkedin.discovery import _parse_guest_query
    assert _parse_guest_query('site:linkedin.com/jobs "Data Engineer" France') == ("Data Engineer", "France")
    assert _parse_guest_query('site:linkedin.com/jobs "BI Developer" "Power BI" France') == ("BI Developer Power BI", "France")
    assert _parse_guest_query('site:linkedin.com/jobs "Microsoft Fabric" data engineer France') == ("Microsoft Fabric data engineer", "France")
    assert _parse_guest_query('site:linkedin.com/jobs "Data Engineer"') == ("Data Engineer", None)
    assert _parse_guest_query('site:linkedin.com/posts "Data Engineer" hiring France') == ("Data Engineer hiring", "France")


def test_parse_guest_cards_against_fixture():
    from job_search_toolkit.linkedin.discovery import _parse_guest_cards
    html = (_FIXTURES / "guest_jobs.html").read_text(encoding="utf-8")
    cards = _parse_guest_cards(html)
    assert len(cards) == 2
    assert cards[0]["url"] == "https://www.linkedin.com/jobs/view/4454183821/"
    assert cards[0]["title"] == "Data Engineer"
    assert "Acme" in cards[0]["snippet"] and "Paris" in cards[0]["snippet"]
    assert cards[1]["url"] == "https://www.linkedin.com/jobs/view/4455012345/"


def test_parse_guest_cards_skips_missing_title():
    from job_search_toolkit.linkedin.discovery import _parse_guest_cards
    html = '<li><div data-entity-urn="urn:li:jobPosting:111"><h4>NoTitle</h4></div></li>'
    assert _parse_guest_cards(html) == []


def test_linkedin_guest_backend_search_uses_endpoint(monkeypatch):
    from job_search_toolkit.linkedin import discovery as dmod
    from job_search_toolkit.linkedin.discovery import LinkedInGuestBackend
    fixture = (_FIXTURES / "guest_jobs.html").read_text(encoding="utf-8")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, text=fixture, request=request)

    _patch_client(monkeypatch, handler)
    backend = LinkedInGuestBackend(max_results=100)
    run = backend.search(('site:linkedin.com/jobs "Data Engineer" France',))
    assert run["backend"] == "linkedin_guest"
    assert run["cost_usd"] is None
    assert "keywords=Data%20Engineer" in captured["url"]
    assert "location=France" in captured["url"]
    assert "start=0" in captured["url"]
    # fixture has 2 cards; ensure no dup across a hypothetical 2nd page within cap
    assert len(run["results"]) == 2


def test_linkedin_guest_backend_dedups_and_stops_on_empty(monkeypatch):
    from job_search_toolkit.linkedin import discovery as dmod
    from job_search_toolkit.linkedin.discovery import LinkedInGuestBackend
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 1:  # second page returns nothing -> stop
            return httpx.Response(200, text="<html></html>", request=request)
        return httpx.Response(200, text="<html></html>", request=request)

    _patch_client(monkeypatch, handler)
    run = LinkedInGuestBackend(max_results=100).search(('site:linkedin.com/jobs "Data Engineer" France',))
    assert run["results"] == []
    assert calls["n"] >= 1


def test_make_backend_guest():
    from job_search_toolkit.linkedin.discovery import LinkedInGuestBackend, make_backend
    for name in ("linkedin_guest", "linkedin", "guest"):
        assert isinstance(make_backend(name), LinkedInGuestBackend)
