"""Tests for the Built In France scraper + adapter (fixtures are real captures)."""

from __future__ import annotations

import json
import httpx
from datetime import date, timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from job_search_toolkit.pipelines.jd.adapt_builtin import normalize_builtin_job
from job_search_toolkit.scrapers import builtin as b

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def list_soup() -> BeautifulSoup:
    html = (FIXTURES / "builtin_list_page.html").read_text(encoding="utf-8")
    return BeautifulSoup(html, "html.parser")


@pytest.fixture()
def detail_html() -> str:
    return (FIXTURES / "builtin_detail_page.html").read_text(encoding="utf-8")


def test_build_url_pagination():
    assert b.build_url() == "https://builtin.com/jobs/eu/france"
    assert b.build_url(3) == "https://builtin.com/jobs/eu/france?page=3"


def test_list_page_extracts_job_links(list_soup):
    cards = b.extract_listing_cards(list_soup)
    hrefs = {c["detail_url"] for c in cards}
    assert len(cards) >= 5
    assert all(h.startswith("https://builtin.com/job/") for h in hrefs)
    # deduped by job id
    ids = [c["job_id"] for c in cards]
    assert len(ids) == len(set(ids))
    first = next(c for c in cards if c["job_id"] == "10873025")
    assert first["title"] == "Lead Product Designer"
    assert "Circle" in (first["company"] or "")
    assert first["workplace_raw"] == "Remote"
    assert first["salary_min_annual"] == 140000.0
    assert first["salary_max_annual"] == 170000.0


def test_scrape_honors_max_pages(monkeypatch, tmp_path, list_soup, detail_html):
    """max_pages bounds listing-page fetches; details only fetched once per job."""
    calls = {"list": 0, "detail": 0}
    fixture_html = list_soup.decode()

    def fake_fetch(client, url):
        if "/job/" in url:
            calls["detail"] += 1
            return detail_html
        calls["list"] += 1
        return fixture_html

    monkeypatch.setattr(b, "fetch_page", fake_fetch)
    out = tmp_path / "jobs.json"
    count = b.scrape(out, max_pages=2)

    assert calls["list"] == 2
    assert count > 0
    assert calls["detail"] == count  # one detail fetch per scraped job


def test_scrape_uses_run_config_cap(monkeypatch, tmp_path, list_soup, detail_html):
    class Cfg:
        builtin_max_pages = 1
        http_timeout = 30.0

    monkeypatch.setattr(b, "get_run_config", lambda: Cfg())
    calls = {"list": 0}

    def fake_fetch(client, url):
        if "/job/" in url:
            return detail_html
        calls["list"] += 1
        return list_soup.decode()

    monkeypatch.setattr(b, "fetch_page", fake_fetch)
    count = b.scrape(tmp_path / "jobs.json", max_pages=None)
    assert calls["list"] == 1  # run-config cap respected
    records = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert len(records) == count


def test_normalize_job_fields(detail_html):
    posting = b.extract_job_posting_jsonld(detail_html)
    raw = {
        "job_id": "10873025",
        "slug": "lead-product-designer",
        "title": "Lead Product Designer",
        "company": "Circle (circle.so)",
        "location_raw": "",
        "workplace_raw": "Remote",
        "posted_text": "2 Hours Ago",
        "detail_url": "https://builtin.com/job/lead-product-designer/10873025",
        "description": None,
        "date_posted": None,
        "employment_type": None,
        **{
            k: None
            for k in ("salary_min_annual", "salary_max_annual")
        },
    }
    # Merge JSON-LD facts the way fetch_job_detail does
    raw["date_posted"] = posting.get("datePosted") or b._parse_relative_date(raw["posted_text"])
    raw["employment_type"] = posting.get("employmentType")

    job = normalize_builtin_job(raw)
    assert job["id"] == "builtin-10873025"
    assert job["source_board"] == "builtin"
    assert job["title"] == "Lead Product Designer"
    assert job["company"] == "Circle (circle.so)"
    assert job["description_language"] == "en"
    assert job["workplace_type"].value == "remote"
    assert job["contract_types"][0].value == "full_time"
    assert job["apply_url"] == raw["detail_url"]
    assert job["date_posted"] and job["date_posted"].startswith("20")


def test_normalize_salary_absent_stays_none():
    job = normalize_builtin_job(
        {
            "job_id": "42",
            "slug": "s",
            "title": "T",
            "company": "C",
            "location_raw": "",
            "workplace_raw": None,
            "posted_text": None,
            "detail_url": "https://builtin.com/job/s/42",
            "description": None,
            "date_posted": None,
            "employment_type": None,
            "salary_min_annual": None,
            "salary_max_annual": None,
        }
    )
    assert job["salary"]["min_annual_eur"] is None
    assert job["salary"]["max_annual_eur"] is None
    assert job["salary"]["is_disclosed"] is False
    assert job["workplace_type"] is None
    assert job["contract_types"] == []


def test_parse_relative_date_variants():
    today = date.today()
    assert b._parse_relative_date("3 Days Ago") == str(today - timedelta(days=3))
    assert b._parse_relative_date("2 Hours Ago") == str(today)
    assert b._parse_relative_date("no date here") is None

# ---------------------------------------------------------------------------
# Hardening fixes
# ---------------------------------------------------------------------------

def test_extract_listing_cards_anchor_without_card():
    """GIVEN a /job/ anchor without a card ancestor, WHEN extracting,
    THEN the record still builds with None card fields (no AttributeError)."""
    soup = BeautifulSoup(
        '<a data-id="job-card-title" href="/job/dev-slug/99900001">Dev</a>',
        "html.parser",
    )
    cards = b.extract_listing_cards(soup)
    assert len(cards) == 1
    rec = cards[0]
    assert rec["title"] == "Dev"
    assert rec["company"] is None
    assert rec["workplace_raw"] is None
    assert rec["salary_raw"] is None
    assert rec["salary_min_annual"] is None


def test_scrape_client_uses_run_config_timeout(monkeypatch, tmp_path):
    """GIVEN scrape creates its client, THEN the RunConfig timeout applies."""
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get(self, url):
            raise httpx.ConnectError("simulated connection failure")

        def close(self):
            pass

    monkeypatch.setattr(b.httpx, "Client", FakeClient)
    count = b.scrape(tmp_path / "jobs.json", max_pages=1)
    assert count == 0  # first listing page failed -> break
    assert "timeout" in captured
    assert captured["timeout"] == b.get_run_config().http_timeout


def test_fetch_page_retries_retriable_statuses(monkeypatch):
    """GIVEN a 503 then a 200, WHEN fetching, THEN it retries and succeeds."""
    class FakeResp:
        def __init__(self, status):
            self.status_code = status
            self.text = "<html>ok</html>"

    class FakeClient:
        def __init__(self):
            self.statuses = iter([503, 200])
            self.calls = 0

        def get(self, url):
            self.calls += 1
            return FakeResp(next(self.statuses))

    sleeps: list[float] = []
    monkeypatch.setattr(b.time, "sleep", sleeps.append)

    client = FakeClient()
    html = b.fetch_page(client, "https://builtin.com/jobs/eu/france")
    assert html == "<html>ok</html>"
    assert client.calls == 2
    assert len(sleeps) == 1  # one backoff sleep before the retry


def test_normalize_defaults_enrichment_gates():
    """H1: enrichment gates must stay NULL so the warehouse enriches them."""
    job = normalize_builtin_job({
        "job_id": "7",
        "slug": "s",
        "title": "T",
        "company": "ACME",
        "location_raw": "",
        "workplace_raw": None,
        "posted_text": None,
        "detail_url": "https://builtin.com/job/s/7",
        "description": None,
        "date_posted": None,
        "employment_type": None,
        "salary_min_annual": None,
        "salary_max_annual": None,
    })
    assert job["engagement_type"] is None
    assert job["posting_company_type"] is None
    assert job["company_info"]["org_type"] is None
    assert job["company_info"]["name"] == "ACME"
    enr = job["_enrichment"]
    assert not any(enr[k] for k in (
        "tech_extracted", "company_researched",
        "vertical_classified", "translated", "scored",
    ))
    assert job["scores"] is None and job["overall_score"] is None
    assert job["_source"]["job_id"] == "7"


def test_normalize_salary_no_fabricated_currency():
    """H2: no source currency -> empty currency_original, unknown stays unknown;
    an explicit currency is passed through untouched."""
    base = {
        "job_id": "9", "slug": "s", "title": "T", "company": "C",
        "location_raw": "", "workplace_raw": None, "posted_text": None,
        "detail_url": "https://builtin.com/job/s/9", "description": None,
        "date_posted": None, "employment_type": None,
    }
    undisclosed = normalize_builtin_job({**base})
    sal = undisclosed["salary"]
    assert sal["is_disclosed"] is False
    assert sal["currency_original"] == ""
    assert sal["min_annual_eur"] is None and sal["max_annual_eur"] is None

    usd = normalize_builtin_job({
        **base,
        "salary_min_annual": 140000.0,
        "salary_max_annual": 170000.0,
        "salary_currency": "USD",
    })
    sal_usd = usd["salary"]
    assert sal_usd["is_disclosed"] is True
    assert sal_usd["currency_original"] == "USD"
    assert sal_usd["min_annual_eur"] == 140000.0


def test_scrape_rejects_non_json_fmt(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        b.scrape(tmp_path / "jobs.json", max_pages=1, fmt="csv")
