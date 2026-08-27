"""
Welcome to the Jungle (WTTJ) job-offer scraper — crawler-sanctioned sitemap route.

Route (verified 2026-08-27):
  1. ``https://www.welcometothejungle.com/sitemaps/index.xml.gz`` lists
     ``job-listings.{0..8}.xml.gz`` sitemaps.
  2. Each child sitemap holds ~10,000 offer URLs; France offers are the ones
     containing ``/fr/companies/`` (skip ``/en/``).
  3. Each France offer page is server-rendered with an ``application/ld+json``
     block of type ``JobPosting`` (title, description, datePosted, baseSalary,
     employmentType, hiringOrganization, jobLocation) plus ``og:title`` /
     ``og:description`` metas.

Bounded crawl: at most ``max_jobs`` offer pages are fetched per run (default
from ``get_run_config().wttj_max_jobs``); the ~60K sitemap URLs are never all
fetched.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path
from typing import Annotated, Literal, Optional
from xml.etree import ElementTree

import typer
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from job_search_toolkit.run_config import get_run_config

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://www.welcometothejungle.com"
SITEMAP_INDEX_URL = f"{BASE_URL}/sitemaps/index.xml.gz"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

logger = logging.getLogger(__name__)

# Offer URL shape: /fr/companies/<company>/jobs/<slug>[_<location>]
FRANCE_MARKER = "/fr/companies/"
ENGLISH_MARKER = "/en/companies/"

_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def build_url(company: str, slug: str) -> str:
    """Build a France offer URL from its company slug and job slug."""
    return f"{BASE_URL}/fr/companies/{company}/jobs/{slug}"


# WTTJ blocks plain httpx by TLS fingerprint and rate-limits aggressive fetches
# (returns HTTP 202), so we impersonate Chrome via curl_cffi and pace requests.
# Retry/backoff come from RunConfig (http_retries/http_backoff).
_MIN_REQUEST_INTERVAL = 1.0  # seconds between HTTP calls (polite pacing)
_RETRIABLE = frozenset({202, 408, 429, 500, 502, 503, 504})
_last_request_at = 0.0


def http_get(url: str) -> bytes | None:
    """GET one URL via curl_cffi (Chrome TLS impersonation). None on failure."""
    global _last_request_at
    rc = get_run_config()
    retries = rc.http_retries
    backoff = rc.http_backoff

    for attempt in range(retries + 1):
        pace = _MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if pace > 0:
            time.sleep(pace)
        _last_request_at = time.monotonic()

        try:
            resp = curl_requests.get(
                url, headers=HEADERS, impersonate="chrome", timeout=rc.http_timeout
            )
        except Exception as exc:  # curl_cffi raises transport/SSL errors
            logger.warning("Fetch failed for %s: %s", url, exc)
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            return None

        if resp.status_code == 200:
            return resp.content
        if resp.status_code in _RETRIABLE and attempt < retries:
            time.sleep(backoff * (attempt + 1))
            continue
        logger.warning("Fetch failed for %s: HTTP %s", url, resp.status_code)
        return None
    return None


def _maybe_gunzip(data: bytes) -> bytes:
    """Decompress gzip payloads, pass plain XML through."""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def parse_sitemap(data: bytes) -> list[str]:
    """Parse a (possibly gzipped) sitemap XML body into its <loc> URL list."""
    root = ElementTree.fromstring(_maybe_gunzip(data))
    locs = [el.text.strip() for el in root.iter(f"{_NS}loc") if el.text]
    # Namespaces vary in the wild; fall back to local-name matching.
    if not locs:
        locs = [el.text.strip() for el in root.iter() if el.tag.endswith("}loc") and el.text]
    return locs


def france_job_urls(locs: list[str]) -> list[str]:
    """Keep deduplicated France offer URLs (`/fr/companies/`, never `/en/`)."""
    seen: set[str] = set()
    out: list[str] = []
    for url in locs:
        if FRANCE_MARKER not in url or ENGLISH_MARKER in url:
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def discover_france_urls(max_jobs: int) -> list[str]:
    """Enumerate up to `max_jobs` France offer URLs from the sitemap route."""
    index_data = http_get(SITEMAP_INDEX_URL)
    if not index_data:
        logger.warning("Sitemap index unavailable; no WTTJ URLs discovered.")
        return []
    children = [u for u in parse_sitemap(index_data) if "job-listings." in u]
    urls: list[str] = []
    for child in children:
        if len(urls) >= max_jobs:
            break
        child_data = http_get(child)
        if not child_data:
            continue
        urls.extend(france_job_urls(parse_sitemap(child_data)))
    return urls[:max_jobs]


def extract_jsonld(html: str | BeautifulSoup) -> list[dict]:
    """Return every parsed JSON-LD object block on the page (dicts only).

    Array-toplevel blocks are skipped rather than breaking downstream
    `b.get("@type")` consumers."""
    soup = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
    blocks = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            blocks.append(data)
    return blocks


def _og_meta(soup: BeautifulSoup, name: str) -> str | None:
    """Return the `content` of `<meta property="og:<name>">` if present."""
    tag = soup.find("meta", attrs={"property": f"og:{name}"})
    if tag is None:
        return None
    content = tag.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


def parse_offer(html_text: str, url: str) -> dict:
    """
    Parse one offer page into a RAW record dict.

    Full path: JSON-LD JobPosting fields + og meta mirror.
    Partial path (no JobPosting block): og:title / og:description only,
    `content_quality="partial"` (mirrors linkedin.parse_job).
    """
    soup = BeautifulSoup(html_text, "html.parser")
    raw: dict = {"url": url}

    posting = next(
        (b for b in extract_jsonld(soup) if b.get("@type") == "JobPosting"),
        None,
    )
    og_title = _og_meta(soup, "title")
    og_description = _og_meta(soup, "description")

    if posting is not None:
        org = posting.get("hiringOrganization") or {}
        locations = posting.get("jobLocation") or []
        first_addr = (
            locations[0].get("address", {}) if isinstance(locations, list) and locations else {}
        )
        location_parts = [
            first_addr.get(k) or ""
            for k in ("addressLocality", "addressRegion", "addressCountry")
        ]
        salary_value = ((posting.get("baseSalary") or {}).get("value") or {})
        unit = salary_value.get("unitText")
        raw.update({
            "content_quality": "full",
            "jobposting": True,
            "title": posting.get("title"),
            "company": org.get("name"),
            "description": posting.get("description"),
            "date_posted": posting.get("datePosted"),
            "employment_type": posting.get("employmentType"),
            "location_raw": ", ".join(p for p in location_parts if p) or None,
            "salary_min": salary_value.get("minValue"),
            "salary_max": salary_value.get("maxValue"),
            "salary_currency": (posting.get("baseSalary") or {}).get("currency"),
            "salary_unit": unit,
        })
    else:
        raw.update({
            "content_quality": "partial",
            "jobposting": False,
            "title": og_title,
            "company": None,
            "description": og_description,
            "date_posted": None,
            "employment_type": None,
            "location_raw": None,
            "salary_min": None,
            "salary_max": None,
            "salary_currency": None,
            "salary_unit": None,
        })

    raw["og_title"] = og_title
    raw["og_description"] = og_description
    return raw


def scrape(output: Path, max_pages: int | None, fmt: str) -> int:
    """
    Core scraping logic: crawl bounded France offers, write RAW records as JSON.

    For WTTJ one "page" is one offer page, so `max_pages` caps the number of
    fetched offer pages (= max_jobs). When None, the cap comes from
    `get_run_config().wttj_max_jobs`. Returns the number of jobs scraped.
    Network errors on individual pages skip that page without aborting.
    """
    cap = max_pages if max_pages is not None else get_run_config().wttj_max_jobs
    if fmt != "json":
        raise ValueError(f"Unsupported fmt {fmt!r}: only 'json' is implemented")

    urls = discover_france_urls(cap)
    records: list[dict] = []
    seen_urls: set[str] = set()
    for url in urls:
        if len(records) >= cap:
            break
        if url in seen_urls:
            continue
        seen_urls.add(url)
        body = http_get(url)
        if not body:
            continue
        try:
            records.append(parse_offer(body.decode("utf-8", errors="replace"), url))
        except Exception as exc:  # one malformed page must not abort the run
            logger.warning("Parse failed for %s: %s", url, exc)

    output.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Scraped %d WTTJ jobs to %s", len(records), output)
    return len(records)


MAX_JOBS_HELP = "Max offer pages to fetch (default: run-config wttj_max_jobs)"


@app.command("wttj")
def wttj(
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output JSON file (raw records)."),
    ] = Path("wttj_jobs.json"),
    max_pages: Annotated[Optional[int], typer.Option("--max-pages", help=MAX_JOBS_HELP)] = None,
    fmt: Annotated[Literal["json"], typer.Option("--fmt")] = "json",
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    """Scrape Welcome to the Jungle France job offers via the sitemap route."""
    scrape(output, max_pages, fmt)


if __name__ == "__main__":
    app()
