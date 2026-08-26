"""
Scrape remote job listings from Remote OK (https://remoteok.com).

Remote OK exposes a public JSON API at https://remoteok.com/api -- GET, no
auth, no rate limiting. The response is a JSON array whose first element is
metadata ({"legal": ..., "last_updated": ...}); the remaining elements are
listings. There is no pagination: a single call returns all listings.

The raw API is NOISY (bus-driver postings, junk listings), so a
required-keyword filter is applied before normalization: a listing must
carry at least one data/tech tag from REQUIRED_TAGS (see below) and must
NOT carry any NOISE_TAGS marker (spam/labour postings spray generic
tags). --query is an ADDITIONAL post-filter on title + tags + description
(all terms must appear); --location post-filters the raw location string.

Salary is raw USD (salary_min / salary_max, 0 = not disclosed) --
normalized to annual EUR at EUR_USD_RATE = 0.92 with
currency_original="USD". workplace_type is REMOTE always (the board is
remote-only). date is an ISO 8601 timestamp (epoch is the Unix-time
fallback) -- normalized to an ISO date.

REQUIRED_TAGS (data/tech keywords; a listing's tags must contain at least
one, matched as whole words):
    data, database, analytics, business intelligence, sql, python, etl,
    machine, ai, ml, devops, cloud, backend, frontend, golang, java,
    javascript, typescript, ruby, rust, c#, c++, aws, azure, kubernetes,
    docker, linux, react, node, django, flask, fullstack, full stack,
    web dev, chatbot, firebase, apache, laravel, math, data annotation

NOISE_TAGS (excluded; junk listings carry these):
    non tech, labourer, assembly, scheme, payroll, virtual assistant,
    customer support, recruiter, supervisor

ToS: the API requires a link back (follow, no nofollow) to the Remote OK
URL and mentions of Remote OK as a source -- see the "legal" metadata
field in the API response. Consumers of this scraper's output must
comply.

Output: canonical CanonicalJob records as JSON or CSV.

Usage:
    uv run python -m job_search_toolkit.scrapers.remoteok
    uv run python -m job_search_toolkit.scrapers.remoteok --query "python"
    uv run python -m job_search_toolkit.scrapers.remoteok --format csv -o remoteok_jobs.csv
"""
import csv
import datetime
import json
import re
from pathlib import Path
from typing import Annotated, Literal, Optional

import httpx
import typer
from bs4 import BeautifulSoup

from job_search_toolkit.schemas import (
    CanonicalJob,
    WorkplaceType,
    new_canonical_job,
)

from job_search_toolkit.run_config import get_run_config

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://remoteok.com/api"
DEFAULT_QUERY = "data engineer"
DEFAULT_LOCATION = ""  # no board-side location filter; --location post-filters
EUR_USD_RATE = 0.92

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Data/tech tag keywords -- a listing must have at least one of these in its
# tags (whole-word match) to survive the noise filter. Kept specific: generic
# tags like "dev" / "engineer" / "c" are too noisy on this API (junk listings
# spray them everywhere).
REQUIRED_TAGS: tuple[str, ...] = (
    "data", "database", "analytics", "business intelligence", "sql", "python",
    "etl", "machine", "ai", "ml", "devops", "cloud", "backend", "frontend",
    "golang", "java", "javascript", "typescript", "ruby", "rust", "c#", "c++",
    "aws", "azure", "kubernetes", "docker", "linux", "react", "node", "django",
    "flask", "fullstack", "full stack", "web dev", "chatbot", "firebase",
    "apache", "laravel", "math", "data annotation",
)

# Junk/spam markers -- listings carrying any of these tags are dropped.
NOISE_TAGS: tuple[str, ...] = (
    "non tech", "labourer", "assembly", "scheme", "payroll",
    "virtual assistant", "customer support", "recruiter", "supervisor",
)

_REQUIRED_PATTERNS = [re.compile(rf"\b{re.escape(k)}\b") for k in REQUIRED_TAGS]
_NOISE_PATTERNS = [re.compile(rf"\b{re.escape(k)}\b") for k in NOISE_TAGS]
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def build_url(query: str, location: str) -> str:
    """Build the API URL.

    Remote OK has no server-side query/location filters -- the URL is
    always the public API endpoint. ``query`` / ``location`` are applied
    as post-filters in :func:`scrape`.
    """
    return BASE_URL


def fetch_page(client: httpx.Client, url: str) -> list[dict]:
    """Fetch the listing array from the Remote OK API."""
    resp = client.get(url, timeout=get_run_config().http_timeout)
    resp.raise_for_status()
    return resp.json()


def has_required_tag(tags: list[str]) -> bool:
    """True if ``tags`` contains a data/tech keyword and no noise marker."""
    joined = " ".join(t.lower() for t in tags)
    if any(p.search(joined) for p in _NOISE_PATTERNS):
        return False
    return any(p.search(joined) for p in _REQUIRED_PATTERNS)


def query_matches(
    position: str, tags: list[str], description_html: str, terms: list[str]
) -> bool:
    """True if every query term appears in title + tags + description."""
    if not terms:
        return True
    description = BeautifulSoup(description_html or "", "html.parser").get_text(" ", strip=True)
    haystack = " ".join([position, " ".join(tags), description]).lower()
    return all(t in haystack for t in terms)


def parse_date(raw_date: object, raw_epoch: object) -> str | None:
    """Normalize the ISO 8601 ``date`` field (epoch as fallback) to ISO date."""
    if raw_date:
        s = str(raw_date).strip()
        if _ISO_DATE_RE.match(s):
            return s[:10]
    if raw_epoch:
        try:
            return datetime.datetime.fromtimestamp(int(raw_epoch)).date().isoformat()
        except (ValueError, OSError, TypeError):
            return None
    return None


def extract_job(raw: dict) -> dict | None:
    """Validate a raw API listing and return the fields we need."""
    if not raw.get("id") or not raw.get("position"):
        return None
    tags = [t for t in (raw.get("tags") or []) if isinstance(t, str)]
    return {
        "id": str(raw["id"]),
        "company": (raw.get("company") or "").strip(),
        "position": (raw.get("position") or "").strip(),
        "tags": tags,
        "description_html": raw.get("description") or "",
        "salary_min": raw.get("salary_min") or 0,
        "salary_max": raw.get("salary_max") or 0,
        "date": raw.get("date"),
        "epoch": raw.get("epoch"),
        "url": raw.get("url") or raw.get("apply_url"),
        "apply_url": raw.get("apply_url") or raw.get("url"),
        "location": (raw.get("location") or "").strip(" \t,"),
        "_source": raw,
    }


def normalize_job(raw: dict) -> CanonicalJob:
    """Convert a raw Remote OK listing into a CanonicalJob."""
    job = new_canonical_job("remoteok")

    description = BeautifulSoup(
        raw.get("description_html") or "", "html.parser"
    ).get_text(" ", strip=True)

    job.update({
        "id": raw.get("id") or "",
        "source_url": raw.get("url"),
        "title": raw.get("position") or "",
        "company": raw.get("company") or "",
        "apply_url": raw.get("apply_url"),
        "location_raw": raw.get("location") or "",
        "workplace_type": WorkplaceType.REMOTE,
        "date_posted": parse_date(raw.get("date"), raw.get("epoch")),
        "description_text": description,
        "description_language": "en",
        "technologies": [
            t for t in raw.get("tags") or []
            if any(p.search(t.lower()) for p in _REQUIRED_PATTERNS)
        ],
        "_source": raw.get("_source") or {},
    })
    if raw.get("company"):
        job["company_info"]["name"] = str(raw["company"])

    # Salary: raw USD (0 = not disclosed) -> annual EUR at EUR_USD_RATE.
    salary = job["salary"]
    s_min, s_max = raw.get("salary_min") or 0, raw.get("salary_max") or 0
    if s_min > 0 or s_max > 0:
        salary["min_annual_eur"] = round(float(s_min) * EUR_USD_RATE) if s_min > 0 else None
        salary["max_annual_eur"] = round(float(s_max) * EUR_USD_RATE) if s_max > 0 else None
        salary["currency_original"] = "USD"
        salary["frequency_original"] = "yearly"
        salary["is_disclosed"] = True
    return job


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


def scrape(query: str, location: str, output: Path, fmt: str) -> int:
    """Core scraping logic. Returns number of jobs scraped."""
    terms = [t for t in query.lower().split() if t]
    loc = location.strip().lower()

    client = httpx.Client(headers=HEADERS, follow_redirects=True)
    data = fetch_page(client, BASE_URL)
    client.close()

    if not data:
        print("No data returned from the Remote OK API")
        return 0

    # The first element is metadata ({"legal": ..., "last_updated": ...}).
    listings = data[1:] if isinstance(data[0], dict) and "legal" in data[0] else data
    print(f"Fetched {len(listings)} listings from the Remote OK API")

    all_jobs: list[CanonicalJob] = []
    seen_ids: set[str] = set()
    skipped_noise = 0
    skipped_query = 0

    for raw in listings:
        card = extract_job(raw)
        if not card:
            continue
        if not has_required_tag(card["tags"]):
            skipped_noise += 1
            continue
        if not query_matches(
            card["position"], card["tags"], card["description_html"], terms
        ):
            skipped_query += 1
            continue
        if loc and loc not in card["location"].lower():
            continue
        if card["id"] in seen_ids:
            continue
        seen_ids.add(card["id"])
        all_jobs.append(normalize_job(card))

    print(f"  Kept {len(all_jobs)} jobs "
          f"({skipped_noise} filtered by noise/tag filter, "
          f"{skipped_query} filtered by query)")

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


@app.command("remoteok")
def remoteok(
    query: Annotated[
        str, typer.Option("--query", "-q", help="Keyword post-filter on title + tags + description")
    ] = DEFAULT_QUERY,
    location: Annotated[
        str, typer.Option("--location", "-l", help="Post-filter on the listing's raw location string")
    ] = DEFAULT_LOCATION,
    contracts: Annotated[
        Optional[list[str]],
        typer.Option("--contracts", "-c", help="Contract types (ignored: no contract filter on Remote OK)"),
    ] = None,
    remote: Annotated[
        Optional[list[str]],
        typer.Option("--remote", "-r", help="Remote types (ignored: Remote OK is remote-only)"),
    ] = None,
    experience: Annotated[
        Optional[list[str]],
        typer.Option("--experience", "-e", help="Experience levels (ignored: no experience filter on Remote OK)"),
    ] = None,
    sort: Annotated[
        Optional[str],
        typer.Option("--sort", "-s", help="Sort order (ignored: API returns newest first)"),
    ] = None,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output path")
    ] = Path("remoteok_jobs.json"),
    max_pages: Annotated[
        Optional[int],
        typer.Option("--max-pages", "-p", help="Ignored: the API returns all listings in one call"),
    ] = None,
    fmt: Annotated[
        Literal["json", "csv"],
        typer.Option("--format", "-f", help="Output format"),
    ] = "json",
) -> None:
    """Scrape job listings from Remote OK.

    Fetches the public JSON API (https://remoteok.com/api -- no auth), then
    applies the required data/tech-tag filter (REQUIRED_TAGS / NOISE_TAGS)
    plus the --query / --location post-filters. The board is remote-only;
    --contracts/--remote/--experience/--sort/--max-pages are accepted for
    CLI parity with the other scrapers but have no effect here.
    """
    for name, value in (("contracts", contracts), ("remote", remote),
                        ("experience", experience), ("sort", sort),
                        ("max_pages", max_pages)):
        if value:
            print(f"Note: --{name} is not supported by Remote OK -- ignored")

    if fmt == "csv" and output == Path("remoteok_jobs.json"):
        output = Path("remoteok_jobs.csv")

    scrape(query, location, output, fmt)


if __name__ == "__main__":
    app()
