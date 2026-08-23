"""Backend-agnostic LinkedIn discovery via paid search APIs.

Two backends behind one interface: Apify (primary) runs a Google SERP actor
and Tavily (fallback) queries its search API. Both return RAW search results
(url/title/snippet); URL-shape filtering lives in ``urls.classify_url``, not
here. API tokens come from the environment (``APIFY_TOKEN`` /
``TAVILY_API_KEY``) or constructor arguments.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from typing import Protocol, TypedDict

import httpx

_APIFY_BASE = "https://api.apify.com"
_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_DEFAULT_ACTOR_ID = "epctex~google-search-scraper"
_TAVILY_RATE_LIMIT_SLEEP = 1.0
_BODY_PREFIX_CHARS = 200
_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"})
_FAILED_STATUSES = frozenset({"FAILED", "TIMED-OUT", "ABORTED"})


class SearchResult(TypedDict):
    """One raw search hit: landing URL, page title, and snippet text."""

    url: str
    title: str
    snippet: str


class DiscoveryRun(TypedDict):
    """Outcome of one discovery pass over a set of queries."""

    backend: str
    results: list[SearchResult]
    cost_usd: float | None
    usage: dict[str, object]


class DiscoveryBackend(Protocol):
    """Interface every discovery backend implements."""

    name: str

    def search(
        self,
        queries: Sequence[str],
        *,
        country_code: str | None = None,
        language_code: str | None = None,
    ) -> DiscoveryRun: ...


def flatten_apify_dataset(items: list[dict]) -> list[SearchResult]:
    """Flatten Apify google-search-scraper dataset items into SearchResults.

    Pre: ``items`` is a list of search-page dicts, each with an
    ``organicResults`` list of dicts carrying ``url``, ``title`` and
    ``description`` keys.
    Post: one SearchResult per organic result, in document order; missing
    fields fall back to the empty string.
    """
    results: list[SearchResult] = []
    for item in items:
        for organic in item.get("organicResults") or []:
            results.append(
                SearchResult(
                    url=organic.get("url") or "",
                    title=organic.get("title") or "",
                    snippet=organic.get("description") or "",
                )
            )
    return results


def flatten_tavily_response(payload: dict) -> list[SearchResult]:
    """Flatten a Tavily ``/search`` response into SearchResults.

    Pre: ``payload`` is a dict with a ``results`` list of dicts carrying
    ``url``, ``title`` and ``content`` keys.
    Post: one SearchResult per entry, in response order; missing fields fall
    back to the empty string.
    """
    results: list[SearchResult] = []
    for hit in payload.get("results") or []:
        results.append(
            SearchResult(
                url=hit.get("url") or "",
                title=hit.get("title") or "",
                snippet=hit.get("content") or "",
            )
        )
    return results


def _check_response(resp: httpx.Response) -> None:
    """Raise RuntimeError with status + body prefix unless ``resp`` is HTTP 200."""
    if resp.status_code != 200:
        raise RuntimeError(
            f"HTTP {resp.status_code}: {resp.text[:_BODY_PREFIX_CHARS]}"
        )


def _request(
    client: httpx.Client, method: str, url: str, **kwargs: object
) -> httpx.Response:
    """Perform a request, converting transport errors and non-200s to RuntimeError."""
    try:
        resp = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"{method} {url}: {exc}") from exc
    _check_response(resp)
    return resp


def _open_client() -> httpx.Client:
    """Open a fresh HTTP client for one discovery pass (test seam)."""
    return httpx.Client()


class ApifyBackend:
    """Google SERP discovery through the Apify REST API (no SDK)."""

    name = "apify"

    def __init__(
        self,
        token: str | None = None,
        actor_id: str | None = None,
        *,
        timeout: float = 180.0,
        poll_interval: float = 5.0,
    ) -> None:
        """Pre: ``token``, ``APIFY_TOKEN``, or ``APIFY_API_TOKEN`` must be set,
        else RuntimeError.

        Post: a backend bound to the given actor (``actor_id``, defaulting to
        ``APIFY_ACTOR_ID`` then ``epctex~google-search-scraper``) and the
        given run-polling knobs.
        """
        self.token = (
            token
            or os.environ.get("APIFY_TOKEN")
            or os.environ.get("APIFY_API_TOKEN")
        )
        if not self.token:
            raise RuntimeError(
                "Apify token not set: pass token= or export APIFY_TOKEN / APIFY_API_TOKEN"
            )
        self.actor_id = actor_id or os.environ.get("APIFY_ACTOR_ID", _DEFAULT_ACTOR_ID)
        self.timeout = timeout
        self.poll_interval = poll_interval

    def search(
        self,
        queries: Sequence[str],
        *,
        country_code: str | None = None,
        language_code: str | None = None,
    ) -> DiscoveryRun:
        """Run the SERP actor over ``queries`` and return flattened results.

        Pre: ``queries`` is a non-empty sequence of search strings.
        Post: DiscoveryRun with backend="apify" and usage tracking keyed by
        run id; raises RuntimeError on actor failure or HTTP errors, and
        TimeoutError when the run does not finish within ``timeout`` seconds.
        """
        with _open_client() as client:
            run_id = self._start_run(client, queries, country_code, language_code)
            usage_total_usd = self._wait_for_run(client, run_id)
            dataset = self._fetch_dataset(client, run_id)
        results = flatten_apify_dataset(dataset)
        return DiscoveryRun(
            backend="apify",
            results=results,
            cost_usd=usage_total_usd,
            usage={
                "run_id": run_id,
                "usage_total_usd": usage_total_usd,
                "n_queries": len(queries),
            },
        )

    def _start_run(
        self,
        client: httpx.Client,
        queries: Sequence[str],
        country_code: str | None,
        language_code: str | None,
    ) -> str:
        """POST the queries to the actor and return the new run id."""
        url = f"{_APIFY_BASE}/v2/acts/{self.actor_id}/runs"
        payload = {
            "queries": "\n".join(queries),
            "countryCode": country_code or "",
            "languageCode": language_code or "",
            "maxPagesPerQuery": 1,
        }
        resp = _request(client, "POST", url, params={"token": self.token}, json=payload)
        return resp.json()["data"]["id"]

    def _wait_for_run(self, client: httpx.Client, run_id: str) -> float | None:
        """Poll the run until it finishes; return usageTotalUsd on success.

        Pre: the run exists and ``self.timeout`` is positive.
        Post: returns the reported usage in USD (None when absent); raises
        RuntimeError for FAILED/TIMED-OUT/ABORTED runs and TimeoutError when
        ``self.timeout`` elapses first.
        """
        url = f"{_APIFY_BASE}/v2/actor-runs/{run_id}"
        deadline = time.monotonic() + self.timeout
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Apify run {run_id} did not finish within {self.timeout}s"
                )
            resp = _request(client, "GET", url, params={"token": self.token})
            data = resp.json()["data"]
            status = data.get("status")
            if status in _TERMINAL_STATUSES:
                if status in _FAILED_STATUSES:
                    raise RuntimeError(f"Apify run {run_id} ended with status {status}")
                return data.get("usageTotalUsd")
            time.sleep(self.poll_interval)

    def _fetch_dataset(self, client: httpx.Client, run_id: str) -> list[dict]:
        """Download the run's dataset items (the raw search pages)."""
        url = f"{_APIFY_BASE}/v2/actor-runs/{run_id}/dataset/items"
        resp = _request(
            client, "GET", url, params={"token": self.token, "format": "json"}
        )
        items = resp.json()
        if not isinstance(items, list):
            raise RuntimeError(
                f"Apify dataset for run {run_id} returned {type(items).__name__}, "
                "expected list"
            )
        return items


class TavilyBackend:
    """LinkedIn-scoped web search discovery through the Tavily API."""

    name = "tavily"

    def __init__(self, api_key: str | None = None, *, max_results: int = 10) -> None:
        """Pre: ``api_key`` or ``TAVILY_API_KEY`` must be set, else RuntimeError.

        Post: a backend querying up to ``max_results`` LinkedIn hits per query.
        """
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "TAVILY_API_KEY is not set: pass api_key= or export TAVILY_API_KEY"
            )
        self.max_results = max_results

    def search(
        self,
        queries: Sequence[str],
        *,
        country_code: str | None = None,
        language_code: str | None = None,
    ) -> DiscoveryRun:
        """Query Tavily once per search string and concatenate the hits.

        Pre: ``queries`` is a non-empty sequence of search strings.
        Post: DiscoveryRun with backend="tavily", cost_usd=None and usage
        counting queries and total results; raises RuntimeError on HTTP/API
        errors. Requests are rate-limited with a 1s pause between queries.
        ``country_code`` and ``language_code`` are accepted for interface
        parity and ignored by this backend.
        """
        all_results: list[SearchResult] = []
        with _open_client() as client:
            for index, query in enumerate(queries):
                payload = {
                    "api_key": self.api_key,
                    "query": query,
                    "include_domains": ["linkedin.com"],
                    "max_results": self.max_results,
                }
                resp = _request(client, "POST", _TAVILY_ENDPOINT, json=payload)
                all_results.extend(flatten_tavily_response(resp.json()))
                if index < len(queries) - 1:
                    time.sleep(_TAVILY_RATE_LIMIT_SLEEP)
        return DiscoveryRun(
            backend="tavily",
            results=all_results,
            cost_usd=None,
            usage={"n_queries": len(queries), "n_results": len(all_results)},
        )


def make_backend(name: str) -> DiscoveryBackend:
    """Construct the discovery backend named ``name``.

    Pre: ``name`` is "apify" or "tavily" (case-insensitive).
    Post: a ready-to-search backend instance; ValueError for unknown names.
    """
    canonical = name.lower()
    if canonical == "apify":
        return ApifyBackend()
    if canonical == "tavily":
        return TavilyBackend()
    raise ValueError(f"Unknown discovery backend: {name!r} (expected 'apify' or 'tavily')")


def discover(
    queries: Sequence[str],
    backend: DiscoveryBackend | str,
    *,
    country_code: str | None = None,
    language_code: str | None = None,
) -> DiscoveryRun:
    """Run discovery with the given backend, given as instance or name.

    Pre: ``backend`` is a DiscoveryBackend instance or "apify"/"tavily".
    Post: the backend's ``search`` result for ``queries``; ValueError for
    unknown backend names.
    """
    resolved = make_backend(backend) if isinstance(backend, str) else backend
    return resolved.search(
        queries, country_code=country_code, language_code=language_code
    )
