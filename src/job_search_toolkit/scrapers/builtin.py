"""
Scraper: builtin.com France job listings.

Crawls https://builtin.com/jobs/eu/france listing pages (?page=N), extracts
the /job/<slug>/<id> detail links from the server-rendered cards, fetches each
detail page, parses its JSON-LD JobPosting block (falls back to the card
fields) and writes RAW record dicts as JSON.

A browser User-Agent is required: plain curl-style UAs get 403.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal, Optional

import httpx
import typer
from bs4 import BeautifulSoup

from job_search_toolkit.run_config import get_run_config

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://builtin.com"
LIST_PATH = "/jobs/eu/france"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en;q=0.9,fr;q=0.8",
}

logger = logging.getLogger(__name__)

JOB_HREF_RE = re.compile(r"^/job/(?P<slug>[^/?#]+)/(?P<job_id>\d+)$")
SALARY_RE = re.compile(r"(?P<min>\d+(?:\.\d+)?)K-(?P<max>\d+(?:\.\d+)?)K\s+Annually")
REL_DATE_RE = re.compile(
    r"\b(?P<n>\d+)\s*(?P<unit>hour|day|week|month)s?\s+ago", re.IGNORECASE
)


def build_url(page: int = 1) -> str:
    """Build a France listings URL with optional ?page=N pagination."""
    url = f"{BASE_URL}{LIST_PATH}"
    return f"{url}?page={page}" if page > 1 else url


_RETRIABLE = frozenset({429, 500, 502, 503, 504})


def fetch_page(client: httpx.Client, url: str) -> str | None:
    """Fetch one page; return its HTML text or None on failure.

    Retries retriable statuses (5xx / 429) with exponential backoff from
    RunConfig (http_retries / http_backoff)."""
    cfg = get_run_config()
    for attempt in range(cfg.http_retries + 1):
        try:
            resp = client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("Fetch failed %s: %s", url, exc)
            return None
        if resp.status_code == 200:
            return resp.text
        if resp.status_code not in _RETRIABLE or attempt == cfg.http_retries:
            logger.warning("Non-200 %s on %s", resp.status_code, url)
            return None
        time.sleep(cfg.http_backoff * (attempt + 1))
    return None


def _parse_salary(text: str) -> tuple[float | None, float | None]:
    """'140K-170K Annually' -> (140000.0, 170000.0); else (None, None)."""
    m = SALARY_RE.search(text or "")
    if not m:
        return None, None
    return float(m.group("min")) * 1000.0, float(m.group("max")) * 1000.0


def _parse_relative_date(text: str) -> str | None:
    """'2 Hours Ago' / '3 Days Ago' -> ISO date relative to today."""
    m = REL_DATE_RE.search(text or "")
    if not m:
        return None
    n, unit = int(m.group("n")), m.group("unit").lower()
    delta = {"hour": timedelta(0), "day": timedelta(days=n),
             "week": timedelta(weeks=n), "month": timedelta(days=30 * n)}[unit]
    return str(date.today() - delta)


def _card_field(card, icon_class: str) -> str | None:
    """Text of the span next to an attribute icon inside one job card.

    Returns None when `card` is None (anchor without a card ancestor)."""
    if card is None:
        return None
    for span in card.find_all("span", class_="text-gray-04"):
        block = span.find_parent("div")
        icon = block.find("i") if block else None
        if icon and icon_class in icon.get("class", []):
            return span.get_text(" ", strip=True)
    return None


def extract_listing_cards(soup: BeautifulSoup) -> list[dict]:
    """Extract raw per-job fields from one listings page (deduped by job id)."""
    cards: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select('a[data-id="job-card-title"]'):
        m = JOB_HREF_RE.match(anchor.get("href", ""))
        if not m:
            continue
        job_id = m.group("job_id")
        if job_id in seen:
            continue
        card = anchor.find_parent(class_="job-bounded-responsive")
        title = anchor.get_text(strip=True)
        company = None
        if card:
            for link in card.find_all("a"):
                if link.get("data-id") != "job-card-title":
                    text = link.get_text(strip=True)
                    if text:
                        company = text
                        break
        location_raw = ""
        if card:
            for span in card.find_all("span", class_="text-gray-04"):
                text = span.get_text(" ", strip=True)
                if not location_raw and re.search(r"FRA$|,", text) and "Locations" not in text \
                        and not SALARY_RE.search(text) and text.lower() not in (
                            "remote", "hybrid", "in-office"):
                    location_raw = text
        posted_text = None
        if card:
            for el in card.find_all(string=REL_DATE_RE):
                posted_text = el.strip()
                break
        salary_min, salary_max = _parse_salary(_card_field(card, "fa-sack-dollar") or "")
        record = {
            "job_id": job_id,
            "slug": m.group("slug"),
            "title": title,
            "company": company,
            "location_raw": location_raw,
            "workplace_raw": _card_field(card, "fa-house-building"),
            "salary_raw": _card_field(card, "fa-sack-dollar"),
            "posted_text": posted_text,
            "detail_url": f"{BASE_URL}/job/{m.group('slug')}/{job_id}",
            "description": None,
            "date_posted": None,
            "employment_type": None,
            "salary_min_annual": salary_min,
            "salary_max_annual": salary_max,
        }
        seen.add(job_id)
        cards.append(record)
    return cards


def extract_job_posting_jsonld(html: str) -> dict | None:
    """Return the JobPosting node of the detail page's JSON-LD @graph."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else []
        for node in graph:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                return node
    return None


def fetch_job_detail(client: httpx.Client, record: dict) -> dict:
    """Fetch the detail page and merge description/date/employment into record."""
    html = fetch_page(client, record["detail_url"])
    if html is None:
        return {"fetch_ok": False}
    posting = extract_job_posting_jsonld(html)
    merged = {
        "fetch_ok": True,
        "description": None,
        "date_posted": posting.get("datePosted") if posting else None,
        "employment_type": posting.get("employmentType") if posting else None,
    }
    if posting:
        description = posting.get("description")
        if isinstance(description, str):
            # Strip HTML tags; keep the plain text.
            merged["description"] = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
    if merged["date_posted"] is None and record.get("posted_text"):
        merged["date_posted"] = _parse_relative_date(record["posted_text"])
    return merged


def scrape(output: Path, max_pages: int | None = None, fmt: str = "json") -> int:
    """Core scraping logic. Writes raw records as JSON to output, returns count."""
    cfg = get_run_config()
    if max_pages is None:
        max_pages = cfg.builtin_max_pages

    client = httpx.Client(
        headers=HEADERS,
        follow_redirects=True,
        timeout=cfg.http_timeout,
    )
    records: list[dict] = []
    seen_ids: set[str] = set()
    try:
        for page in range(1, max_pages + 1):
            html = fetch_page(client, build_url(page))
            if html is None:
                break
            page_cards = extract_listing_cards(BeautifulSoup(html, "html.parser"))
            new = [r for r in page_cards if r["job_id"] not in seen_ids]
            print(f"  Page {page}: {len(page_cards)} links ({len(new)} new)")
            for record in new:
                seen_ids.add(record["job_id"])
                record.update(fetch_job_detail(client, record))
                if not record.pop("fetch_ok", True):
                    logger.warning("Skipping %s (detail fetch failed)", record["detail_url"])
                    continue
                records.append(record)
    finally:
        client.close()

    if fmt != "json":
        raise ValueError(f"Unsupported fmt {fmt!r}: only 'json' is implemented")
    output.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {len(records)} jobs to {output}")
    return len(records)


@app.command("builtin")
def builtin(
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output file path")
    ] = Path("builtin_jobs.json"),
    max_pages: Annotated[
        Optional[int],
        typer.Option("--max-pages", "-p", help="Limit to N listing pages"),
    ] = None,
    fmt: Annotated[
        Literal["json"],
        typer.Option("--format", "-f", help="Output format"),
    ] = "json",
) -> None:
    """Scrape Built In France job listings into raw JSON records."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    scrape(output, max_pages=max_pages, fmt=fmt)


if __name__ == "__main__":
    app()
