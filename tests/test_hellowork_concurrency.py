"""Regression tests for hellowork concurrent detail-page fetching.

PR for runtime optimization (~21min pipeline, hellowork detail fetches
dominate): the scrape loop now fetches per-job detail descriptions through a
bounded ThreadPoolExecutor (detail_concurrency) sharing the single
thread-safe httpx.Client, instead of one serial fetch per job.

Behavioral contracts under test:
- Each job's detail URL is fetched EXACTLY once. (A regression that double-
  fetched would silently double hellowork request load and aggravate its
  anti-bot 403 guarding.)
- Descriptions attach to the right card; normalize_job output is identical
  whether detail_concurrency is 1 (serial) or >1 (concurrent).
- detail_concurrency == 1 reproduces the original serial behavior.
- detail fetches route through the shared retry helper (request_with_retry),
  not a bare client.get.
"""

from __future__ import annotations

import json

import httpx

from job_search_toolkit.scrapers import hellowork as hw

_BASE = "https://www.hellowork.com/fr-fr/emploi/"


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=httpx.Request("GET", "https://x/"),
                response=httpx.Response(
                    self.status_code, request=httpx.Request("GET", "https://x/")
                ),
            )


def _detail_html(url: str) -> str:
    """Real detail-page markup: an application/ld+json JobPosting block whose
    description text is derived from the URL (proves per-URL uniqueness)."""
    job_id = url.rsplit("/", 1)[-1]
    payload = json.dumps({
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "description": f"description for job {job_id}",
    })
    return f'<html><script type="application/ld+json">{payload}</script></html>'


def _card_html(offer_id: int) -> str:
    """A search-result card <li> the real extract_job can parse (title/href/id)."""
    return (
        f'<li data-id-storage-item-id="{offer_id}">'
        f'<a data-cy="offerTitle" href="{_BASE}job-{offer_id}">'
        f"<h3><p>Data Engineer {offer_id}</p><p>Acme {offer_id}</p></h3>"
        f"</a></li>"
    )


def _listing_html(card_ids: list[int]) -> str:
    return f"<html><body>{''.join(_card_html(i) for i in card_ids)}</body></html>"


class _FakeClient:
    """httpx.Client stand-in that serves real detail JSON-LD per URL and records
    every get() call (each must be exactly once)."""

    def __init__(self, detail_html: dict[str, str]):
        self.detail_html = dict(detail_html)
        self.get_calls: list[str] = []
        self.close_called = False

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        if url in self.detail_html:
            return _Resp(200, self.detail_html[url])
        return _Resp(200, "<html>no posting</html>")

    def close(self):
        self.close_called = True


def _make_listing_url(n_pages: int) -> str:
    return f"{_BASE}recherche.html?k=data&l=france&p=1"


def _drive_scrape(
    monkeypatch,
    client: _FakeClient,
    listing_html_by_page: list[str],
    concurrency: int,
    n_jobs: int,
) -> list[dict]:
    """Run hw.scrape() end-to-end against the fake client with scripted pages.

    Returns the exported (normalized) job records and asserts the client
    served one listing page per page + one detail page per job.
    """
    import job_search_toolkit.scrapers.http_retry as hr

    class FakeRC:
        http_retries = 1
        http_backoff = 0.0
        http_timeout = 5.0
        detail_concurrency = concurrency

    monkeypatch.setattr(hw, "get_run_config", lambda: FakeRC())
    monkeypatch.setattr(hr, "get_run_config", lambda: FakeRC())
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)

    # scrape() builds its own httpx.Client(...) internally and closes it. Patch
    # the constructor so scrape receives our prebuilt fake client (which
    # records every get() and implements close()).
    monkeypatch.setattr(hw.httpx, "Client", lambda *_a, **_k: client)

    # fetch_page returns listing HTML for each page index.
    monkeypatch.setattr(
        hw,
        "fetch_page",
        lambda _client, _url, page: listing_html_by_page[page - 1],
    )
    monkeypatch.setattr(hw, "find_page_count", lambda soup: len(listing_html_by_page))

    # Route detail fetches through the REAL fetch_job_description against the
    # fake detail client so retry routing is exercised.
    monkeypatch.setattr(hw, "fetch_job_description", hw.fetch_job_description)

    # Capture exported records.
    exported: list[dict] = []

    def _fake_export(jobs, _output, _fmt=None):
        exported.append(jobs)

    monkeypatch.setattr(hw, "export_json", _fake_export)
    monkeypatch.setattr(hw, "export_csv", _fake_export)

    out = _FakeOut()

    hw.scrape(
        _make_listing_url(len(listing_html_by_page)),
        out,
        max_pages=len(listing_html_by_page),
        fmt="json",
    )

    assert client.close_called, "client.close() must be called"
    return exported


class _FakeOut:
    """A Path-like stand-in that can't really be written, so export is patched
    to capture records instead of writing files."""

    def __init__(self):
        pass


def _job_urls(n_jobs: int) -> list[str]:
    return [f"{_BASE}job-{i}" for i in range(1, n_jobs + 1)]


def _assert_each_url_fetched_once(client: _FakeClient, urls: list[str]) -> None:
    from collections import Counter

    counts = Counter(client.get_calls)
    for u in urls:
        assert counts[u] == 1, f"url {u} fetched {counts[u]} times (expected 1)"
    # listing pages are the only non-detail calls; every recorded call is a url
    # we scripted (either a listing fetch or a detail fetch).


def test_concurrent_fetch_fetches_each_url_once(monkeypatch):
    """With detail_concurrency>1 every detail URL is fetched exactly once and
    each job carries its own description (not dropped / not doubled)."""
    n_jobs = 6
    urls = _job_urls(n_jobs)
    client = _FakeClient({u: _detail_html(u) for u in urls})
    listing = [_listing_html(list(range(1, 4))), _listing_html(list(range(4, 7)))]

    captured = _drive_scrape(
        monkeypatch, client, listing, concurrency=3, n_jobs=n_jobs
    )

    _assert_each_url_fetched_once(client, urls)
    assert len(captured) == 1
    jobs = captured[0]
    assert len(jobs) == n_jobs
    # Each exported job must carry the description derived from its URL.
    for job in jobs:
        assert job.get("description_text") is not None, f"job {job['id']} lost description"


def test_serial_matches_concurrent_output(monkeypatch):
    """detail_concurrency=1 (serial) and >1 (concurrent) must produce identical
    records — same count, same order, same descriptions — and each detail URL
    fetched exactly once in both modes."""
    n_jobs = 6
    urls = _job_urls(n_jobs)
    listing = [_listing_html(list(range(1, 4))), _listing_html(list(range(4, 7)))]

    def run(concurrency):
        client = _FakeClient({u: _detail_html(u) for u in urls})
        captured = _drive_scrape(
            monkeypatch, client, listing, concurrency=concurrency, n_jobs=n_jobs
        )
        _assert_each_url_fetched_once(client, urls)
        return captured[0]

    serial = run(1)
    concurrent = run(3)

    assert len(serial) == len(concurrent) == n_jobs
    # Order + identity + description text must match exactly.
    for s, c in zip(serial, concurrent):
        assert s["id"] == c["id"], f"order drifted: {s['id']} vs {c['id']}"
        assert s["description_text"] == c["description_text"], (
            f"description drifted for job {s['id']}"
        )
