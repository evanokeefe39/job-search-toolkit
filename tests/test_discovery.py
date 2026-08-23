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
_ACTOR = "epctex/google-search-scraper"


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
