"""Tests for the WTTJ sitemap scraper + adapter (Slice A).

Fixtures under tests/fixtures/wttj/ are derived from REAL responses captured
2026-08-27 (sitemap index, job-listings.0.xml.gz sample, offer page HTML).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_search_toolkit.scrapers import wttj
from job_search_toolkit.pipelines.jd.adapt_wttj import normalize_wttj_job

FIXTURES = Path(__file__).parent / "fixtures" / "wttj"


# ---------------------------------------------------------------------------
# Sitemap parsing / France filter
# ---------------------------------------------------------------------------

def test_parse_sitemap_keeps_france_urls():
    """GIVEN a gzipped child sitemap with fr+en locs, WHEN parsing,
    THEN only deduplicated France offer URLs are kept."""
    data = (FIXTURES / "job-listings-sample.xml.gz").read_bytes()
    locs = wttj.parse_sitemap(data)
    assert any("/fr/companies/" in u for u in locs)
    assert any("/en/companies/" in u for u in locs)  # fixture mixes both on purpose

    fr = wttj.france_job_urls(locs)
    assert fr, "France URLs must survive the filter"
    assert all("/fr/companies/" in u for u in fr)
    assert not any("/en/companies/" in u for u in fr)
    assert len(fr) == len(set(fr))  # deduplicated


def test_parse_sitemap_index_lists_job_listings_children():
    """GIVEN the real sitemap index, WHEN parsing, THEN the job-listings
    children are enumerated."""
    index = (FIXTURES / "sitemap_index.xml").read_text(encoding="utf-8")
    locs = wttj.parse_sitemap(index.encode("utf-8"))
    children = [u for u in locs if "job-listings." in u]
    assert len(children) == 9  # job-listings.0..8
    assert children[0].endswith("job-listings.0.xml.gz")


def test_parse_sitemap_gzip_failure_returns_zero(monkeypatch):
    """GIVEN an unreachable sitemap index, WHEN scraping, THEN 0 (no crash)."""
    monkeypatch.setattr(wttj, "http_get", lambda url: None)
    out = Path("out.json")
    assert wttj.scrape(out, max_pages=3, fmt="json") == 0


# ---------------------------------------------------------------------------
# Offer page parsing
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def full_raw():
    html = (FIXTURES / "offer_jsonld.html").read_text(encoding="utf-8")
    url = ("https://www.welcometothejungle.com/fr/companies/momji/jobs/"
           "conseiller-clientele-h-f-cdd_angers_GEKCM_5OZMoaR")
    return wttj.parse_offer(html, url)


def test_parse_offer_jsonld(full_raw):
    """GIVEN a real offer page with a JobPosting block, WHEN parsing,
    THEN title/company/description/date/location come from JSON-LD."""
    raw = full_raw
    assert raw["content_quality"] == "full"
    assert raw["title"] == "Conseiller clientèle H/F CDD"
    assert "GROUPE EPIKTET" in raw["company"]
    assert raw["date_posted"] == "2026-07-22T22:01:13Z"
    assert "Angers" in raw["location_raw"]
    assert raw["employment_type"] == "TEMPORARY"
    # Monthly salary captured as reported
    assert raw["salary_min"] == 2095 and raw["salary_max"] == 2295
    assert raw["salary_unit"] == "MONTHLY"
    assert "Conseiller clientèle" in (raw["og_title"] or "")


def test_parse_offer_fallback_og_meta():
    """GIVEN an offer page without a JobPosting block, WHEN parsing,
    THEN og:title / og:description fill title/description and quality=partial."""
    html = (FIXTURES / "offer_no_jsonld.html").read_text(encoding="utf-8")
    assert "application/ld+json" not in html  # fixture precondition
    raw = wttj.parse_offer(html, "https://www.welcometothejungle.com/fr/companies/x/jobs/y_paris")
    assert raw["content_quality"] == "partial"
    assert raw["date_posted"] is None
    assert raw["company"] is None


def test_normalize_wttj_job_full(full_raw):
    """Canonical mapping of a full JSON-LD record."""
    job = normalize_wttj_job(full_raw)
    assert job["source_board"] == "wttj"
    assert job["id"] == job["source_url"] == job["apply_url"] == full_raw["url"]
    assert job["title"] == "Conseiller clientèle H/F CDD"
    assert job["description_language"] == "fr"
    assert "Angers" in job["location_raw"]
    from job_search_toolkit.schemas import ContractType as CT
    assert job["contract_types"] == [CT.TEMPORARY]  # employmentType TEMPORARY maps cleanly
    # monthly salary annualised ×12
    assert job["salary"]["min_annual_eur"] == pytest.approx(2095 * 12)
    assert job["salary"]["max_annual_eur"] == pytest.approx(2295 * 12)
    assert job["salary"]["frequency_original"] == "MONTHLY"
    assert job["salary"]["is_disclosed"] is True
    assert job["_enrichment"]["tech_extracted"] is False


def test_normalize_never_fabricates_missing_fields():
    """Partial record: missing salary/location/date stay empty — no invention."""
    raw = {
        "url": "https://www.welcometothejungle.com/fr/companies/acme/jobs/dev",
        "content_quality": "partial",
        "title": "Ingénieur logiciel",
        "company": None,
        "description": None,
        "date_posted": None,
        "employment_type": None,
        "location_raw": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_unit": None,
    }
    job = normalize_wttj_job(raw)
    assert job["salary"]["is_disclosed"] is False
    assert job["salary"]["min_annual_eur"] is None
    assert job["salary"]["max_annual_eur"] is None
    assert job["location_raw"] == ""
    assert job["date_posted"] is None
    assert job["workplace_type"] is None
    assert job["contract_types"] == []
    assert job["company_info"]["hq_country"] == "FR"  # France URL implies FR HQ


# ---------------------------------------------------------------------------
# Bounded crawl
# ---------------------------------------------------------------------------

class _FakeCrawl:
    """Stands in for http_get: serves fixtures, counts fetches."""

    def __init__(self, offer_html: str, n_offers: int):
        self.offer_count = 0
        self.n_offers = n_offers
        self.index = (FIXTURES / "sitemap_index.xml").read_bytes()
        self.child = (FIXTURES / "job-listings-sample.xml.gz").read_bytes()
        self.offer = offer_html.encode("utf-8")

    def __call__(self, url: str) -> bytes | None:
        if url.endswith("index.xml.gz"):
            return self.index
        if ".xml.gz" in url:
            return self.child
        self.offer_count += 1
        return self.offer


def test_scrape_honors_max_jobs(monkeypatch, tmp_path, full_raw):
    """GIVEN max_pages set, WHEN crawling, THEN at most that many offer pages
    are fetched (never the full sitemap)."""
    fake = _FakeCrawl((FIXTURES / "offer_jsonld.html").read_text(encoding="utf-8"), 3)
    monkeypatch.setattr(wttj, "http_get", fake)

    out = tmp_path / "wttj.json"
    count = wttj.scrape(out, max_pages=2, fmt="json")

    assert fake.offer_count == 2
    assert count == 2
    records = json.loads(out.read_text(encoding="utf-8"))
    assert len(records) == 2
    assert records[0]["url"].startswith("https://www.welcometothejungle.com/fr/companies/")
    assert records[0]["title"] == full_raw["title"]


def test_scrape_uses_run_config_cap(monkeypatch, tmp_path):
    """GIVEN no explicit max, THEN the cap comes from get_run_config()."""
    class _Cfg:
        wttj_max_jobs = 3

    fake = _FakeCrawl((FIXTURES / "offer_jsonld.html").read_text(encoding="utf-8"), 5)
    monkeypatch.setattr(wttj, "http_get", fake)
    import job_search_toolkit.scrapers.wttj as m
    monkeypatch.setattr(m, "get_run_config", lambda: _Cfg())

    out = tmp_path / "wttj.json"
    count = wttj.scrape(out, max_pages=None, fmt="json")

    assert fake.offer_count == 3
    assert count == 3


def test_scrape_skips_failing_offer_pages(monkeypatch, tmp_path):
    """GIVEN one failing offer, WHEN crawling, THEN it is skipped (not aborting)."""
    fake = _FakeCrawl(
        (FIXTURES / "offer_jsonld.html").read_text(encoding="utf-8"), 3
    )
    orig_child = fake.child

    def flaky(url):
        data = fake(url)
        if fake.offer_count == 1:
            return None  # simulate network failure on first offer
        return data

    monkeypatch.setattr(wttj, "http_get", flaky)
    out = tmp_path / "wttj.json"
    count = wttj.scrape(out, max_pages=3, fmt="json")
    assert count >= 1  # remaining offers still scraped
    assert out.exists()

# ---------------------------------------------------------------------------
# Hardening / robustness fixes
# ---------------------------------------------------------------------------

def test_extract_jsonld_skips_array_blocks():
    """GIVEN an ld+json block whose top level is a JSON array, WHEN parsing,
    THEN it is dropped (no AttributeError on b.get later)."""
    html = (
        '<html><head>'
        '<script type="application/ld+json">[{"@type": "Thing"}]</script>'
        '<script type="application/ld+json">{"@type": "JobPosting"}</script>'
        '</head><body></body></html>'
    )
    blocks = wttj.extract_jsonld(html)
    assert len(blocks) == 1
    assert blocks[0]["@type"] == "JobPosting"


def test_parse_offer_survives_array_jsonld():
    """GIVEN a page with only array-toplevel ld+json, WHEN parsing,
    THEN partial og-meta path is used and no exception escapes."""
    html = (
        '<html><head>'
        '<script type="application/ld+json">["a", {"@type": "Thing"}]</script>'
        '<meta property="og:title" content="Dev Python">'
        '</head><body></body></html>'
    )
    raw = wttj.parse_offer(html, "https://www.welcometothejungle.com/fr/companies/x/jobs/y")
    assert raw["content_quality"] == "partial"
    assert raw["title"] == "Dev Python"


def test_scrape_skips_unparsable_pages(monkeypatch, tmp_path):
    """GIVEN one offer whose parse raises, WHEN crawling,
    THEN that page is skipped without aborting the run."""
    fake = _FakeCrawl(
        (FIXTURES / "offer_jsonld.html").read_text(encoding="utf-8"), 3
    )
    real_parse = wttj.parse_offer

    def failing_then_ok(html_text, url):
        if fake.offer_count == 1:
            raise ValueError("boom")
        return real_parse(html_text, url)

    monkeypatch.setattr(wttj, "http_get", fake)
    monkeypatch.setattr(wttj, "parse_offer", failing_then_ok)
    out = tmp_path / "wttj.json"
    count = wttj.scrape(out, max_pages=3, fmt="json")
    assert count == 2  # first offer skipped, the rest scraped


def test_normalize_annual_salary_unit_kept():
    """GIVEN a YEAR-unit salary, WHEN normalizing, THEN values are kept ×1."""
    job = normalize_wttj_job({
        "url": "https://www.welcometothejungle.com/fr/companies/acme/jobs/dev",
        "title": "Dev",
        "salary_min": 40000,
        "salary_max": 50000,
        "salary_currency": "EUR",
        "salary_unit": "YEAR",
    })
    sal = job["salary"]
    assert sal["min_annual_eur"] == 40000.0
    assert sal["max_annual_eur"] == 50000.0
    assert sal["is_disclosed"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
