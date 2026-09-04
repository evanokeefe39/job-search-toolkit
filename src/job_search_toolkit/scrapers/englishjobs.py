"""
Scrape English-speaking job listings from englishjobs.fr.

English Jobs France is a job board for English-speaking roles in France
(all postings are in English). Server-rendered HTML -- no JS/Playwright
needed. There is no per-query ?q= search: the site's real URL structure
(per its llms.txt) is:

    /jobs/{query}                query-only search
    /in/{location}               location-only (city or region slug)
    /in/{location}/{query}       location + query

Paginates via ?page=N (20 cards per page). Each card carries the full
description inline -- there is no detail page; the card's clickout link
redirects to the employer ATS (talent.com).

Output: canonical CanonicalJob records as JSON or CSV.

Usage:
    uv run python -m job_search_toolkit.scrapers.englishjobs
    uv run python -m job_search_toolkit.scrapers.englishjobs --query "python developer" --location lyon
    uv run python -m job_search_toolkit.scrapers.englishjobs --url "https://www.englishjobs.fr/in/paris/data%20engineer"
"""
import csv
import json
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Optional
from urllib.parse import quote, urljoin

import httpx
import typer
from bs4 import BeautifulSoup, Tag

from job_search_toolkit.schemas import (
    CanonicalJob,
    ContractType,
    EngagementType,
    WorkplaceType,
    new_canonical_job,
)

from job_search_toolkit.run_config import get_run_config
from job_search_toolkit.scrapers.http_retry import request_with_retry

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://www.englishjobs.fr"
DEFAULT_QUERY = "data engineer"
DEFAULT_LOCATION = "paris"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

# English month names / 3-letter abbreviations -> month number
MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# Contract keywords found in card title/description text, most specific first.
# CDI -> full_time (permanent), CDD -> temporary (fixed-term), etc.
CONTRACT_KEYWORDS: list[tuple[ContractType, tuple[str, ...]]] = [
    (ContractType.PART_TIME, ("part-time", "part time", "temps partiel", "mi-temps")),
    (ContractType.INTERNSHIP, ("internship", "intern", "stage", "apprenticeship", "alternance", "apprenti")),
    (ContractType.CONTRACT, ("freelance", "contractor", "independent contract", "consultant contract")),
    (ContractType.TEMPORARY, ("cdd", "fixed-term", "fixed term", "temporary contract", "temps determiné", "intérim", "interim")),
    (ContractType.SEASONAL, ("seasonal", "saisonnier")),
    (ContractType.VOLUNTEER, ("volunteer", "bénévolat", "benevolat")),
    (ContractType.FULL_TIME, ("cdi", "permanent", "full-time", "full time", "unlimited contract", "temps plein", "durée indéterminée")),
]
DATE_RE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2})$")


def build_url(query: str, location: str) -> str:
    """Build the search URL from parameters.

    englishjobs.fr has no ?q= search param; the real routes are
    /jobs/{query}, /in/{location} and /in/{location}/{query}.
    """
    if location:
        path = f"/in/{quote(location)}"
        if query:
            path += f"/{quote(query)}"
    elif query:
        path = f"/jobs/{quote(query)}"
    else:
        path = "/jobs"
    return f"{BASE_URL}{path}"


def fetch_page(client: httpx.Client, url: str) -> str:
    resp = request_with_retry(client, "GET", url, timeout=get_run_config().http_timeout)
    resp.raise_for_status()
    return resp.text


def detect_contracts(text: str) -> list[str]:
    """Map contract keywords found in card text to ContractType values."""
    found: list[str] = []
    for contract_type, keywords in CONTRACT_KEYWORDS:
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", text, re.IGNORECASE):
                found.append(contract_type.value)
                break
    return found


def detect_workplace(text: str) -> WorkplaceType | None:
    """Detect remote/hybrid/onsite from card text (no structured field)."""
    low = text.lower()
    if "remote" in low:
        return WorkplaceType.REMOTE
    if "hybrid" in low:
        return WorkplaceType.HYBRID
    if "on-site" in low or "onsite" in low:
        return WorkplaceType.ONSITE
    return None


def parse_date(raw: str | None) -> str | None:
    """Parse 'August 4' (month day, no year) into ISO 8601.

    Year is resolved to the most recent occurrence: current year unless the
    date is still in the future, in which case the previous year.
    """
    if not raw:
        return None
    match = DATE_RE.match(raw.strip())
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    day = int(match.group(2))
    today = date.today()
    try:
        parsed = date(today.year, month, day)
    except ValueError:
        return None
    if parsed > today:
        parsed = date(today.year - 1, month, day)
    return parsed.isoformat()


def extract_job(card: Tag) -> dict[str, object] | None:
    """Extract all fields from a job card container."""
    title_el = card.select_one("h3[itemprop=title]") or card.select_one("h3")
    if not title_el:
        return None
    title = title_el.get_text(" ", strip=True)
    link = card.select_one("a.js-joblink") or card.find(
        "a", href=lambda h: h and "/clickout/" in h
    )
    if not link:
        return None

    # The card is the whole listing: clickout link is both permalink and ATS link
    clickout_url = urljoin(BASE_URL, link.get("href", ""))

    # <ul> children are always [company, location, date], e.g. 'Natobotics',
    # 'Paris', 'August 4' -- classify defensively by content instead of index.
    company: str | None = None
    location: str | None = None
    date_posted: str | None = None
    for li in card.select("ul li"):
        text = li.get_text(" ", strip=True)
        if not text:
            continue
        if date_posted is None and DATE_RE.match(text):
            date_posted = text
        elif company is None:
            company = text
        elif location is None:
            location = text

    # Full description sits inline in the card (site has no detail pages).
    desc_div = card.find("div", class_=lambda c: c and "text-gray-400" in str(c))
    description = desc_div.get_text(" ", strip=True) if desc_div else None

    card_text = " ".join(
        t for t in (title, company or "", description or "") if t
    )
    return {
        "id": card.get("id"),
        "title": title,
        "company": company,
        "location": location,
        "date_posted": date_posted,
        "url": clickout_url,
        "description": description,
        "contract_types": detect_contracts(card_text),
        "workplace_type": detect_workplace(card_text),
    }


def normalize_job(raw: dict[str, object]) -> CanonicalJob:
    """Convert a raw englishjobs.fr card into a CanonicalJob."""
    job = new_canonical_job("englishjobs")
    job.update({
        "id": raw.get("id") or "",
        "source_url": raw.get("url"),
        "title": raw.get("title") or "",
        "company": raw.get("company") or "",
        "apply_url": raw.get("url"),
        "location_raw": raw.get("location") or "",
        "workplace_type": raw.get("workplace_type"),
        "date_posted": parse_date(raw.get("date_posted")),
        "contract_types": raw.get("contract_types") or [],
        "description_text": raw.get("description") or "",
        "description_language": "en",
        "engagement_type": EngagementType.DIRECT,
        "_source": raw,
    })
    if raw.get("company"):
        job["company_info"]["name"] = str(raw["company"])
    return job


def find_page_count(soup: BeautifulSoup) -> int:
    """Extract total pages from pagination links (?page=N)."""
    max_page = 1
    for a in soup.select("a[href]"):
        match = re.search(r"[?&]page=(\d+)", a.get("href", ""))
        if match:
            max_page = max(max_page, int(match.group(1)))
    return max_page


def flatten_canonical(job: CanonicalJob) -> dict:
    """Flatten a CanonicalJob into a CSV-friendly flat dict."""
    s = job.get("salary") or {}
    c = job.get("company_info") or {}
    return {
        "id": job.get("id"),
        "source_board": job.get("source_board"),
        "title": job.get("title"),
        "company": job.get("company"),
        "apply_url": job.get("apply_url"),
        "location_raw": job.get("location_raw"),
        "workplace_type": job.get("workplace_type"),
        "date_posted": job.get("date_posted"),
        "salary_min_annual_eur": s.get("min_annual_eur"),
        "salary_max_annual_eur": s.get("max_annual_eur"),
        "salary_currency_original": s.get("currency_original"),
        "salary_is_disclosed": s.get("is_disclosed"),
        "contract_types": "|".join(job.get("contract_types", [])),
        "contract_duration": job.get("contract_duration"),
        "seniority_level": job.get("seniority_level"),
        "role_category": job.get("role_category"),
        "years_experience_min": job.get("years_experience_min"),
        "technologies": "|".join(job.get("technologies", [])),
        "competencies": "|".join(job.get("competencies", [])),
        "description_language": job.get("description_language"),
        "company_industry": "|".join(c.get("industry", [])),
        "company_size": c.get("size_employees"),
        "company_founded": c.get("year_founded"),
        "company_hq_country": c.get("hq_country"),
        "company_type": c.get("org_type"),
        "engagement_type": job.get("engagement_type"),
        "posting_company_type": job.get("posting_company_type"),
        "end_client_name": job.get("end_client_name"),
        "end_client_sector": job.get("end_client_sector"),
        "views": job.get("views"),
        "applications": job.get("applications"),
        "is_expired": job.get("is_expired"),
        "overall_score": job.get("overall_score"),
        "recommendation_tier": job.get("recommendation_tier"),
    }


def scrape(list_url: str, output: Path, max_pages: int | None, fmt: str) -> int:
    """Core scraping logic. Returns number of jobs scraped."""
    client = httpx.Client(headers=HEADERS, follow_redirects=True)

    html = fetch_page(client, list_url)
    soup = BeautifulSoup(html, "html.parser")
    total_pages = find_page_count(soup)
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)
    print(f"Found {total_pages} pages")

    all_jobs: list[CanonicalJob] = []
    seen_ids: set[str] = set()

    for page in range(1, total_pages + 1):
        if page > 1:
            html = fetch_page(client, f"{list_url}?page={page}")
            soup = BeautifulSoup(html, "html.parser")

        page_jobs = 0
        for card in soup.select("div.job"):
            raw = extract_job(card)
            if not raw or not raw.get("title"):
                continue
            job_id = raw.get("id")
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)  # type: ignore[arg-type]
            all_jobs.append(normalize_job(raw))
            page_jobs += 1

        print(f"  Page {page}: {page_jobs} jobs")

    client.close()

    if fmt == "json":
        with open(output, "w", encoding="utf-8") as f:
            json.dump(all_jobs, f, ensure_ascii=False, indent=2)
    else:
        flat = [flatten_canonical(j) for j in all_jobs]
        fieldnames = list(flat[0].keys()) if flat else []
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat)

    print(f"\nWrote {len(all_jobs)} jobs to {output}")
    return len(all_jobs)


@app.command("englishjobs")
def englishjobs(
    query: Annotated[
        str, typer.Option("--query", "-q", help="Job title / keyword search")
    ] = DEFAULT_QUERY,
    location: Annotated[
        str, typer.Option("--location", "-l", help="City or region slug (paris, lyon, ile-de-france)")
    ] = DEFAULT_LOCATION,
    contracts: Annotated[
        Optional[list[str]],
        typer.Option("--contracts", "-c", help="Contract types (ignored: no contract filter on englishjobs.fr)"),
    ] = None,
    remote: Annotated[
        Optional[list[str]],
        typer.Option("--remote", "-r", help="Remote types (ignored: no remote filter on englishjobs.fr)"),
    ] = None,
    experience: Annotated[
        Optional[list[str]],
        typer.Option("--experience", "-e", help="Experience levels (ignored: no experience filter on englishjobs.fr)"),
    ] = None,
    sort: Annotated[
        Optional[str],
        typer.Option("--sort", "-s", help="Sort order (ignored: board sorts by recency)"),
    ] = None,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output path")
    ] = Path("englishjobs_jobs.json"),
    url: Annotated[
        Optional[str],
        typer.Option(
            "--url", "-u",
            help="Full search URL from browser (overrides query/location)",
        ),
    ] = None,
    max_pages: Annotated[
        Optional[int],
        typer.Option("--max-pages", "-p", help="Limit to N pages"),
    ] = None,
    fmt: Annotated[
        Literal["json", "csv"],
        typer.Option("--format", "-f", help="Output format"),
    ] = "json",
) -> None:
    """Scrape job listings from englishjobs.fr.

    Defaults to data engineer jobs in Paris. The board has no server-side
    contract/remote/experience filters -- those flags are accepted for CLI
    parity with the other scrapers but are ignored here.
    """
    for name, value in (("contracts", contracts), ("remote", remote),
                        ("experience", experience), ("sort", sort)):
        if value:
            print(f"Note: --{name} is not supported by englishjobs.fr -- ignored")

    if fmt == "csv" and output == Path("englishjobs_jobs.json"):
        output = Path("englishjobs_jobs.csv")

    if url:
        list_url = url
        print(f"Using URL: {list_url}")
    else:
        list_url = build_url(query, location)
        print(f"Search URL: {list_url}")

    scrape(list_url, output, max_pages, fmt)


if __name__ == "__main__":
    app()
