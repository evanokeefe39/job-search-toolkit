"""Backend-agnostic LinkedIn discovery via paid search APIs.

Two backends behind one interface: Apify (primary) runs a Google SERP actor
and Tavily (fallback) queries its search API. Both return RAW search results
(url/title/snippet); URL-shape filtering lives in ``urls.classify_url``, not
here. API tokens come from the environment (``APIFY_TOKEN`` /
``TAVILY_API_KEY``) or constructor arguments.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Sequence
from datetime import timedelta
from typing import Protocol, TypedDict
from urllib.parse import quote

import httpx
from apify_client import ApifyClient
from apify_client.errors import ApifyApiError

_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_DEFAULT_ACTOR_ID = "apify~google-search-scraper"
_TAVILY_RATE_LIMIT_SLEEP = 1.0
_BODY_PREFIX_CHARS = 200
_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"})
_FAILED_STATUSES = frozenset({"FAILED", "TIMED-OUT", "ABORTED"})

# LinkedIn public guest jobs search endpoint (no auth). Returns 10 job cards
# per page as HTML fragments; paginate with start=0,25,50,...
_GUEST_JOBS_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_GUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/126 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_GUEST_PAGE_SIZE = 10            # cards per guest-API page
_GUEST_START_STEP = 25           # start offset increments
_GUEST_DEFAULT_MAX_RESULTS = 100
# Country code (lowercase) -> default location text for the guest API.
_DEFAULT_LOCATION_BY_COUNTRY = {"fr": "France"}
_QUOTED_RE = re.compile(r'"([^"]+)"')


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
    """Raise RuntimeError with status + body prefix unless ``resp`` is 2xx.

    The Apify run-start endpoint returns 201 Created; GET endpoints return 200.
    """
    if not 200 <= resp.status_code < 300:
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


def _run_info_dict(run: object) -> dict:
    """Coerce an apify-client Run (a pydantic model) or plain dict to a dict.

    The SDK's ``start``/``wait_for_finish``/``get`` return a ``Run`` pydantic
    model (not subscriptable); ``model_dump(by_alias=True)`` yields the API's
    camelCase keys (``usageTotalUsd``, ``defaultDatasetId``). Plain dicts pass
    through unchanged.
    """
    if hasattr(run, "model_dump"):
        return run.model_dump(by_alias=True)  # type: ignore[attr-defined]
    return dict(run)


class ApifyBackend:
    """Google SERP discovery through the official Apify SDK (``apify-client``).

    Uses the SDK's ``actor().start`` / ``run().wait_for_finish`` /
    ``dataset().iterate_items`` rather than hand-rolled REST calls.
    """

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
        ``APIFY_ACTOR_ID`` then ``apify~google-search-scraper``) and the
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
        run id; raises RuntimeError on actor failure or API errors, and
        TimeoutError when the run does not finish within ``timeout`` seconds.
        """
        run_input: dict[str, object] = {
            "queries": "\n".join(queries),
            "maxPagesPerQuery": 1,
        }
        # The official apify/google-search-scraper input uses country/language,
        # not the epctex actor's countryCode/languageCode. Omit when unset.
        if country_code:
            run_input["country"] = country_code
        if language_code:
            run_input["language"] = language_code

        client = ApifyClient(self.token)
        run_id = self._start_run(client, run_input)
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

    def _start_run(self, client: object, run_input: dict[str, object]) -> str:
        """Start the actor and return the new run id."""
        run_info = client.actor(self.actor_id).start(run_input=run_input)  # type: ignore[attr-defined]
        return _run_info_dict(run_info)["id"]

    def _wait_for_run(self, client: object, run_id: str) -> float | None:
        """Poll the run until it finishes; return usageTotalUsd on success.

        Pre: the run exists and ``self.timeout`` is positive.
        Post: returns the reported usage in USD (None when absent); raises
        RuntimeError for FAILED/TIMED-OUT/ABORTED runs and TimeoutError when
        ``self.timeout`` elapses first.
        """
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Apify run {run_id} did not finish within {self.timeout}s"
                )
            try:
                run_info = client.run(run_id).wait_for_finish(  # type: ignore[attr-defined]
                    wait_duration=timedelta(seconds=min(self.poll_interval, remaining))
                )
            except ApifyApiError as exc:
                raise RuntimeError(f"Apify run {run_id} failed: {exc}") from exc
            if not run_info:
                continue
            status = _run_info_dict(run_info).get("status")
            if status in _TERMINAL_STATUSES:
                if status in _FAILED_STATUSES:
                    raise RuntimeError(f"Apify run {run_id} ended with status {status}")
                return _run_info_dict(run_info).get("usageTotalUsd")

    def _fetch_dataset(self, client: object, run_id: str) -> list[dict]:
        """Download the run's dataset items (the raw search pages)."""
        run_info = client.run(run_id).get()  # type: ignore[attr-defined]
        dataset_id = (_run_info_dict(run_info) if run_info else {}).get("defaultDatasetId")
        if not dataset_id:
            raise RuntimeError(f"Apify run {run_id} has no default dataset")
        return list(client.dataset(dataset_id).iterate_items())  # type: ignore[attr-defined]


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


class LinkedInGuestBackend:
    """LinkedIn's public guest jobs search API, fetched directly (no auth).

    LinkedIn's ``/jobs-guest/jobs/api/seeMoreJobPostings/search`` endpoint
    returns 10 job cards per page as HTML fragments for a keyword + location.
    This backend paginates it and flattens the cards into ``SearchResult``s
    whose ``url`` is the individual ``/jobs/view/<id>/`` listing. Free (no
    paid API), but the endpoint is undocumented and may change or be blocked;
    see ``docs/linkedin-source-spike.md``.
    """

    name = "linkedin_guest"

    def __init__(self, max_results: int = _GUEST_DEFAULT_MAX_RESULTS) -> None:
        self.max_results = max_results

    def search(
        self,
        queries: Sequence[str],
        *,
        country_code: str | None = None,
        language_code: str | None = None,
    ) -> DiscoveryRun:
        client = _open_client()
        results: list[SearchResult] = []
        seen: set[str] = set()
        try:
            for query in queries:
                keywords, location = _parse_guest_query(query)
                location = location or _DEFAULT_LOCATION_BY_COUNTRY.get(
                    (country_code or "").lower(), "France"
                )
                start = 0
                while len(results) < self.max_results:
                    url = (
                        f"{_GUEST_JOBS_ENDPOINT}?keywords={quote(keywords)}"
                        f"&location={quote(location)}&start={start}"
                    )
                    resp = _request(client, "GET", url, headers=_GUEST_HEADERS)
                    page = _parse_guest_cards(resp.text)
                    if not page:
                        break
                    fresh = 0
                    for hit in page:
                        if hit["url"] not in seen:
                            seen.add(hit["url"])
                            results.append(hit)
                            fresh += 1
                    start += _GUEST_START_STEP
                    if len(page) < _GUEST_PAGE_SIZE or fresh == 0:
                        break
        finally:
            client.close()
        return DiscoveryRun(
            backend=self.name,
            results=results,
            cost_usd=None,
            usage={"provider": "linkedin_guest", "n_queries": len(queries), "n_results": len(results)},
        )


def _parse_guest_query(query: str) -> tuple[str, str | None]:
    """Split a LinkedIn search query into ``(keywords, location)``.

    Accepts the ``site:linkedin.com/jobs "<role>" [<keywords>] <place>`` shape:
    quoted phrases become keywords, the final unquoted token is the location,
    and any intermediate unquoted tokens join the keywords. Returns
    ``(keywords, None)`` when no trailing location token exists.
    """
    q = re.sub(r"site:linkedin\.com/(jobs|posts)", " ", query).strip()
    quoted = _QUOTED_RE.findall(q)
    rest = _QUOTED_RE.sub(" ", q).split()
    location = rest[-1] if rest else None
    keywords = quoted + (rest[:-1] if rest else [])
    return " ".join(keywords).strip(), location


def _parse_guest_cards(html_text: str) -> list[SearchResult]:
    """Parse LinkedIn guest-jobs API HTML fragments into SearchResults.

    Each job card is an ``<li>`` carrying ``data-entity-urn="urn:li:jobPosting:
    <id>"`` plus title/company/location fields. Cards without a job id or title
    are skipped. The result URL is the canonical ``/jobs/view/<id>/``.
    """
    out: list[SearchResult] = []
    for block in re.split(r"<li", html_text):
        id_m = re.search(r"urn:li:jobPosting:(\d+)", block)
        if not id_m:
            continue
        job_id = id_m.group(1)
        title_m = re.search(
            r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3>',
            block, re.S,
        )
        if not title_m:
            continue
        title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
        company_m = re.search(
            r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>(.*?)</h4>',
            block, re.S,
        )
        company = re.sub(r"<[^>]+>", "", company_m.group(1)).strip() if company_m else ""
        loc_m = re.search(
            r'class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</span>',
            block, re.S,
        )
        location = re.sub(r"<[^>]+>", "", loc_m.group(1)).strip() if loc_m else ""
        out.append(
            SearchResult(
                url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                title=title,
                snippet=f"{company} — {location}".strip(" —"),
            )
        )
    return out


def make_backend(name: str) -> DiscoveryBackend:
    """Construct the discovery backend named ``name``.

    Pre: ``name`` is "apify", "tavily", or "linkedin_guest" (case-insensitive).
    Post: a ready-to-search backend instance; ValueError for unknown names.
    """
    canonical = name.lower()
    if canonical == "apify":
        return ApifyBackend()
    if canonical == "tavily":
        return TavilyBackend()
    if canonical in ("linkedin", "linkedin_guest", "guest"):
        return LinkedInGuestBackend()
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
    unknown backend names. Accepts "linkedin_guest" for the guest jobs API.
    """
    resolved = make_backend(backend) if isinstance(backend, str) else backend
    return resolved.search(
        queries, country_code=country_code, language_code=language_code
    )
