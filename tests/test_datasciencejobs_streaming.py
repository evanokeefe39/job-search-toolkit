"""Resilience tests for the datasciencejobs scraper (per-job / per-page /
write-what-you-have).

These exercise `scrape()` against synthetic HTML with `fetch_page` /
`fetch_detail` monkeypatched so no network or DuckDB warehouse is involved.
"""

import json
from pathlib import Path

import pytest

from job_search_toolkit.scrapers.datasciencejobs import (
    BASE_URL,
    scrape,
)


def _page_html(start_id: int, count: int, page_num: int, total_pages: int) -> str:
    """Build a synthetic datasciencejobs listing page with `count` job cards.

    Each card yields a stable id ``job-<n>`` that `extract_job` derives from
    the detail href. Pagination links cover pages 1..total_pages so
    `find_page_count` returns the full board size.
    """
    cards = []
    for i in range(count):
        jid = f"job-{start_id + i}"
        cards.append(
            '<div class="card-grid-2">'
            f'<h4><a href="/jobs/{jid}/">Data Engineer {jid}</a></h4>'
            '<a class="name-job">Acme</a>'
            '<span class="location-small">Remote</span>'
            '<span class="card-briefcase">fulltime</span>'
            '<span class="card-text-price">$100000</span>'
            "</div>"
        )
    pagination = "".join(
        f'<a class="page-number" href="/jobs/page/{p}/">{p}</a>'
        for p in range(1, total_pages + 1)
    )
    return (
        "<html><body>"
        + "".join(cards)
        + f'<div class="pagination">{pagination}</div>'
        + "</body></html>"
    )


def _ids_from_output(path: Path) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [job["id"] for job in json.load(f)]


def _listing(pages, total_pages, *, fail_on_page=None):
    """Return a fetch_page replacement serving the synthetic board.

    `pages` maps page number -> (start_id, count). Raises on the page number
    equal to `fail_on_page` (mimicking a DNS failure on that page's URL).
    """

    def fake(client, url):
        if url.rstrip("/") == f"{BASE_URL}/jobs":
            p = 1
        else:
            p = int(url.rstrip("/").split("/")[-1])
        if p == fail_on_page:
            raise RuntimeError(f"simulated DNS failure on page {p}")
        start_id, count = pages[p]
        return _page_html(start_id, count, p, total_pages)

    return fake


@pytest.fixture()
def out_file(tmp_path: Path) -> Path:
    return tmp_path / "board.json"


@pytest.fixture(autouse=True)
def _identity_detail(monkeypatch):
    """Default: fetch_detail is a passthrough (details already on the card)."""

    def identity(client, raw):
        return raw

    monkeypatch.setattr("job_search_toolkit.scrapers.datasciencejobs.fetch_detail", identity)


def test_one_bad_job_does_not_kill_page(monkeypatch, out_file):
    # One page, three cards; the middle card's detail fetch raises.
    total_pages = 1
    pages = {1: (1, 3)}
    monkeypatch.setattr(
        "job_search_toolkit.scrapers.datasciencejobs.fetch_page",
        _listing(pages, total_pages),
    )

    def bad_detail(client, raw):
        if raw["id"] == "job-2":
            raise RuntimeError("simulated detail failure")
        return raw

    monkeypatch.setattr(
        "job_search_toolkit.scrapers.datasciencejobs.fetch_detail", bad_detail
    )

    count = scrape(
        f"{BASE_URL}/jobs/",
        out_file,
        max_pages=None,
        fmt="json",
        query="data",
    )

    assert count == 2
    assert _ids_from_output(out_file) == ["job-1", "job-3"]


def test_page_failure_keeps_prior_pages_no_raise(monkeypatch, out_file):
    # Three pages; page 2 fails. Pages 1 only should persist, no raise.
    total_pages = 3
    pages = {1: (1, 2), 2: (3, 2), 3: (5, 2)}
    monkeypatch.setattr(
        "job_search_toolkit.scrapers.datasciencejobs.fetch_page",
        _listing(pages, total_pages, fail_on_page=2),
    )

    count = scrape(
        f"{BASE_URL}/jobs/",
        out_file,
        max_pages=None,
        fmt="json",
        query="data",
    )

    assert count == 2
    assert _ids_from_output(out_file) == ["job-1", "job-2"]


def test_fresh_run_truncates_stale_partial(monkeypatch, out_file):
    # A stale partial array from a prior crashed run must be overwritten.
    out_file.write_text(
        json.dumps([{"id": "stale-1", "title": "Stale"}, {"id": "stale-2"}], indent=2),
        encoding="utf-8",
    )

    total_pages = 1
    pages = {1: (1, 2)}
    monkeypatch.setattr(
        "job_search_toolkit.scrapers.datasciencejobs.fetch_page",
        _listing(pages, total_pages),
    )

    count = scrape(
        f"{BASE_URL}/jobs/",
        out_file,
        max_pages=None,
        fmt="json",
        query="data",
    )

    assert count == 2
    assert _ids_from_output(out_file) == ["job-1", "job-2"]
    assert "stale-1" not in out_file.read_text(encoding="utf-8")


def test_full_run_matches_old_behavior_same_ids(monkeypatch, out_file):
    # A clean full run must produce every board id exactly once (no dupes via
    # seen_ids) and be stable across runs.
    total_pages = 2
    pages = {1: (1, 2), 2: (3, 2)}
    monkeypatch.setattr(
        "job_search_toolkit.scrapers.datasciencejobs.fetch_page",
        _listing(pages, total_pages),
    )

    count = scrape(
        f"{BASE_URL}/jobs/",
        out_file,
        max_pages=None,
        fmt="json",
        query="data",
    )
    first = _ids_from_output(out_file)

    assert count == 4
    assert first == ["job-1", "job-2", "job-3", "job-4"]
    assert len(first) == len(set(first))  # no dupes

    # Re-running the same board is deterministic (same ids, same order).
    count2 = scrape(
        f"{BASE_URL}/jobs/",
        out_file,
        max_pages=None,
        fmt="json",
        query="data",
    )
    assert count2 == 4
    assert _ids_from_output(out_file) == first


def test_empty_board_returns_empty_array(monkeypatch, out_file):
    # Board with no job cards -> [] (not a crash), count 0.
    total_pages = 1
    pages = {1: (1, 0)}
    monkeypatch.setattr(
        "job_search_toolkit.scrapers.datasciencejobs.fetch_page",
        _listing(pages, total_pages),
    )

    count = scrape(
        f"{BASE_URL}/jobs/",
        out_file,
        max_pages=None,
        fmt="json",
        query="data",
    )

    assert count == 0
    assert json.loads(out_file.read_text(encoding="utf-8")) == []


def test_max_pages_bounds_pages(monkeypatch, out_file):
    # Board advertises 3 pages but max_pages=2 caps the fetch at page 2.
    total_pages = 3
    pages = {1: (1, 2), 2: (3, 2), 3: (5, 2)}
    seen_urls: list[str] = []

    def recording(client, url):
        seen_urls.append(url)
        return _listing(pages, total_pages)(client, url)

    monkeypatch.setattr(
        "job_search_toolkit.scrapers.datasciencejobs.fetch_page", recording
    )

    count = scrape(
        f"{BASE_URL}/jobs/",
        out_file,
        max_pages=2,
        fmt="json",
        query="data",
    )

    assert count == 4
    assert _ids_from_output(out_file) == ["job-1", "job-2", "job-3", "job-4"]
    assert all("/jobs/page/3/" not in url for url in seen_urls)
