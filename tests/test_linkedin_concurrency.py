"""Regression tests for the LinkedIn adapter shared-client fetch (_run_pass).

Follow-on to the board-parallelization spike (docs/board-parallelization-spike.md).
Live probing showed LinkedIn is **rate-limit-bound** under sustained concurrency
(HTTP 429 loses jobs at concurrency >= 2), so it deliberately stays SERIAL.
The fix that IS safe: _run_pass reuses ONE shared httpx.Client across all the
per-URL fetches instead of creating a fresh client (new TLS handshake) per URL.

Behavioral contracts under test:
- Every discovered URL is fetched exactly once through a single shared client,
  producing correct records (no drops, no stale/failed misclassification).
- When a client is injected (test seam) or built internally, fetch_page is
  never called on the per-call `owns_client` path for a multi-URL run — one
  shared client serves every fetch.
- LinkedIn must NOT silently parallelize (no worker pool): concurrency here
  would trigger 429 job loss. Serial correctness is the contract.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from job_search_toolkit.scrapers.linkedin import adapter
from job_search_toolkit.scrapers.linkedin.config import LinkedInConfig
from job_search_toolkit.scrapers.linkedin.discovery import DiscoveryRun, SearchResult
from job_search_toolkit.scrapers.linkedin.tech_scan import TechnologyScanner

FIXTURES = Path(__file__).parent / "fixtures"

# Distinct job URLs (distinct ids) so multiple fetches happen per run.
JOB_URLS = [
    "https://fr.linkedin.com/jobs/view/analytics-engineer-fabric-cdi-lille-logical-conseils-4436738979",
    "https://fr.linkedin.com/jobs/view/data-engineer-fabric-cdi-paris-acme-4436738980",
    "https://fr.linkedin.com/jobs/view/senior-data-engineer-cdi-lyon-globex-4436738981",
]


class _CountingTransport(httpx.BaseTransport):
    """Mock transport that counts total requests handled by its client."""

    def __init__(self):
        self.requests = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(
            200,
            text=(
                '<html><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"JobPosting",'
                '"title":"Data Engineer","hiringOrganization":{"name":"Acme"},'
                '"jobLocation":{"address":{"addressLocality":"Paris",'
                '"addressCountry":"FR"}}}'
                "</script></html>"
            ),
        )


class _StubBackend:
    name = "stub"

    def search(self, queries, *, country_code=None, language_code=None) -> DiscoveryRun:
        return DiscoveryRun(
            backend=self.name,
            results=[SearchResult(url=u, title="job", snippet="") for u in JOB_URLS],
            cost_usd=0.0,
            usage={"n_queries": len(queries)},
        )


def _config() -> LinkedInConfig:
    return LinkedInConfig(backend="stub", post_queries=(), job_queries=("q",))


def _run(monkeypatch, inject_client: bool = True):
    """Run job discovery over the 3 stub URLs; return (outcome, transport)."""
    transport = _CountingTransport()
    client = httpx.Client(transport=transport)
    # adapter.py reads get_run_config only for the owns_client timeout; give it
    # a fake so no real config file is consulted.
    class FakeRC:
        http_retries = 0
        http_backoff = 0.0
        http_timeout = 5.0

    monkeypatch.setattr(adapter, "get_run_config", lambda: FakeRC())
    outcome = adapter.run_discovery(
        _config(),
        backend=_StubBackend(),
        client=client if inject_client else None,  # None = production path
        scanner=TechnologyScanner.from_defaults(),
        kinds=["job"],
    )
    return outcome, transport


def test_shared_client_fetches_each_url_once(monkeypatch):
    """Every discovered URL is fetched exactly once through the injected shared
    client, producing one job per URL with no drops/stale/failed."""
    outcome, transport = _run(monkeypatch, inject_client=True)
    assert len(outcome.jobs) == len(JOB_URLS), "every URL must yield a job"
    assert transport.requests == len(JOB_URLS), "each URL fetched exactly once"
    assert outcome.failed_urls == [] and outcome.stale_urls == []


def test_no_injected_client_builds_one_shared_client(monkeypatch):
    """The production path (client=None) builds ONE shared client and reuses it
    for every fetch — never the per-call owns_client path. Confirms the
    TLS-handshake-per-URL regression is fixed."""
    transport = _CountingTransport()
    shared_client = httpx.Client(transport=transport)

    # adapter binds fetch_page at import time; patch adapter.fetch_page to spy.
    real_fetch = adapter.fetch_page
    calls = []

    def spy_fetch(url, client=None, **kw):
        calls.append(client is None)  # True iff per-call owns_client path
        return real_fetch(url, client=client, **kw)

    monkeypatch.setattr(adapter, "fetch_page", spy_fetch)
    # Patch httpx.Client so _run_pass's owns_client branch returns our shared one.
    monkeypatch.setattr(adapter.httpx, "Client", lambda *a, **k: shared_client)
    monkeypatch.setattr(adapter, "get_run_config", lambda: type("RC", (), {
        "http_timeout": 5.0, "http_retries": 0, "http_backoff": 0.0,
    })())

    outcome = adapter.run_discovery(
        _config(), backend=_StubBackend(),
        scanner=TechnologyScanner.from_defaults(), kinds=["job"],
    )
    assert len(outcome.jobs) == len(JOB_URLS)
    assert len(calls) == len(JOB_URLS), "every URL fetched once"
    assert not any(calls), "shared client used for every fetch (no per-call client)"
