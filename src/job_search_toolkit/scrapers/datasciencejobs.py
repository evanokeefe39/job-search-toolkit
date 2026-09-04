"""
Scrape job listings from datasciencejobs.com.

DataScienceJobs is a dedicated data-board (AI, ML, Data Science, Data
Engineering, Deep Learning, Computer Vision). Server-rendered HTML -- no
JS/Playwright needed. Live probe findings:

- Listing page: https://datasciencejobs.com/jobs/ -- 10 job cards per page
  in ``div.card-grid-2`` containers. Each card carries title, company
  (``a.name-job``), location (``span.location-small``), contract badge
  (``span.card-briefcase``, e.g. "fulltime"), skill tags, and salary
  (``span.card-text-price``, e.g. "$185000 - $205000" or "EUR 75000").
- Detail page: ``/jobs/<slug>-<id>/`` -- full description in
  ``div.content-single`` plus a JSON-LD JobPosting script with ``datePosted``,
  ``employmentType``, ``hiringOrganization`` and structured ``jobLocation``.
  Cards have no description or date, so each card's detail page is fetched.
- Pagination: path-based ``/jobs/page/N/`` (345 pages observed), last page
  number in ``a.page-number`` links.
- There is NO server-side search: ``?q=`` and ``?location=`` are ignored
  (redirect back to /jobs/). ``--query`` is applied as a post-filter on job
  titles (a card passes if any query word appears in the title).

Output: canonical CanonicalJob records as JSON or CSV.

Usage:
    uv run python -m job_search_toolkit.scrapers.datasciencejobs --query "python" --max-pages 2
    uv run python -m job_search_toolkit.scrapers.datasciencejobs --format csv
"""
import csv
import html
import json
import re
from pathlib import Path
from typing import Annotated, Literal, Optional
from urllib.parse import urljoin

import httpx
import typer
from bs4 import BeautifulSoup, Tag

from job_search_toolkit.schemas import (
    CanonicalJob,
    ContractType,
    EngagementType,
    Salary,
    SeniorityLevel,
    WorkplaceType,
    new_canonical_job,
)

from job_search_toolkit.run_config import get_run_config
from job_search_toolkit.scrapers.http_retry import request_with_retry

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://datasciencejobs.com"
DEFAULT_QUERY = "data engineer"
DEFAULT_LOCATION = ""

# Fixed EUR/USD rate for salary normalization (same convention as hiringcafe)
EUR_USD_RATE = 0.92

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# span.card-briefcase text -> ContractType
CONTRACT_NORM_MAP: dict[str, ContractType] = {
    "fulltime": ContractType.FULL_TIME,
    "parttime": ContractType.PART_TIME,
    "internship": ContractType.INTERNSHIP,
    "contract": ContractType.CONTRACT,
    "temporary": ContractType.TEMPORARY,
    "seasonal": ContractType.SEASONAL,
    "volunteer": ContractType.VOLUNTEER,
}

# Detail-page "Job level" -> SeniorityLevel
SENIORITY_NORM_MAP: dict[str, SeniorityLevel] = {
    "entry level": SeniorityLevel.ENTRY,
    "junior level": SeniorityLevel.JUNIOR,
    "medium level": SeniorityLevel.MID,
    "senior level": SeniorityLevel.SENIOR,
    "lead level": SeniorityLevel.LEAD,
    "manager level": SeniorityLevel.MANAGER,
}

# salary text like "$185000 - $205000", "EUR 75000 - EUR 90000", "CAD $123000"
SALARY_RE = re.compile(
    r"(?:(CAD|USD|EUR|GBP)\s*)?([$€£])?\s*([\d][\d,]*)(?:\s*[-–]\s*(?:(?:CAD|USD|EUR|GBP)\s*)?(?:[$€£])?\s*([\d][\d,]*))?"
)
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def build_url(query: str, location: str) -> str:
    """Build the search URL from parameters.

    datasciencejobs.com ignores ``?q=``/``?location=`` server-side (verified
    live: both redirect back to /jobs/), so the listing URL is always the
    jobs index; ``query``/``location`` act as post-filters instead.
    """
    return f"{BASE_URL}/jobs/"


def fetch_page(client: httpx.Client, url: str) -> str:
    resp = request_with_retry(client, "GET", url, timeout=get_run_config().http_timeout)
    resp.raise_for_status()
    return resp.text


def parse_salary(text: str | None) -> Salary:
    """Parse a card salary string into a Salary record.

    Supports "$185000 - $205000", "EUR 75000 - EUR 90000" and
    "CAD $123000 - CAD $180400". USD is converted to EUR at EUR_USD_RATE
    (same convention as hiringcafe); EUR stays as-is. Original currency is
    preserved in ``currency_original``.
    """
    if not text:
        return Salary(
            min_annual_eur=None,
            max_annual_eur=None,
            currency_original="USD",
            frequency_original="yearly",
            is_disclosed=False,
        )
    match = SALARY_RE.search(text)
    if not match:
        return Salary(
            min_annual_eur=None,
            max_annual_eur=None,
            currency_original="USD",
            frequency_original="yearly",
            is_disclosed=False,
        )
    code, symbol, min_raw, max_raw = match.groups()
    currency = (code or CURRENCY_SYMBOLS.get(symbol) or "USD").upper()
    rate = EUR_USD_RATE if currency == "USD" else 1.0
    min_val = int(min_raw.replace(",", "")) if min_raw else None
    max_val = int(max_raw.replace(",", "")) if max_raw else None
    if min_val is None and max_val is None:
        return Salary(
            min_annual_eur=None,
            max_annual_eur=None,
            currency_original=currency,
            frequency_original="yearly",
            is_disclosed=False,
        )
    return Salary(
        min_annual_eur=round(min_val * rate) if min_val is not None else None,
        max_annual_eur=round(max_val * rate) if max_val is not None else None,
        currency_original=currency,
        frequency_original="yearly",
        is_disclosed=True,
    )


def detect_workplace(text: str) -> WorkplaceType | None:
    """Detect remote/hybrid/onsite from location text (no structured field)."""
    low = text.lower()
    if "remote" in low:
        return WorkplaceType.REMOTE
    if "hybrid" in low:
        return WorkplaceType.HYBRID
    if "on-site" in low or "onsite" in low or "on site" in low:
        return WorkplaceType.ONSITE
    return None


def clean_location(text: str) -> str:
    """Collapse whitespace / stray commas in the location span text."""
    parts = [p.strip().strip(",") for p in text.split(",")]
    return ", ".join(p for p in parts if p)


def title_matches(title: str, query: str) -> bool:
    """Post-filter: card passes if ANY query word appears in the title.

    The board has no server-side search, so --query is applied client-side
    (a data-only board: every listing is data/ML/AI adjacent, so a strict
    AND filter would drop most cards on page 1).
    """
    if not query or not query.strip():
        return True
    low = title.lower()
    return any(w in low for w in query.lower().split() if w)


def extract_job(card: Tag) -> dict[str, object] | None:
    """Extract all fields from a job card container."""
    link = card.select_one("h4 a[href]")
    if not link:
        return None
    title = link.get_text(" ", strip=True)
    detail_href = link.get("href", "")
    if not detail_href:
        return None
    detail_url = urljoin(BASE_URL, detail_href)
    job_id = detail_href.rstrip("/").split("/")[-1] or detail_href

    company_el = card.select_one("a.name-job")
    company = company_el.get_text(" ", strip=True) if company_el else None

    loc_el = card.select_one("span.location-small")
    location = clean_location(loc_el.get_text(" ", strip=True)) if loc_el else ""

    apply_el = card.select_one("a[href$='/apply/']")
    apply_url = urljoin(BASE_URL, apply_el.get("href", "")) if apply_el else None

    briefcase_el = card.select_one("span.card-briefcase")
    contract_raw = (
        briefcase_el.get_text(" ", strip=True).lower() if briefcase_el else None
    )

    skills = [
        b.get_text(" ", strip=True)
        for b in card.select("div.skill-tags-compact a.badge")
        if b.get_text(" ", strip=True)
    ]

    price_el = card.select_one("span.card-text-price")
    salary_text = price_el.get_text(" ", strip=True) if price_el else None

    return {
        "id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "detail_url": detail_url,
        "apply_url": apply_url,
        "contract_raw": contract_raw,
        "skills": skills,
        "salary_text": salary_text,
    }


def fetch_detail(client: httpx.Client, raw: dict[str, object]) -> dict[str, object]:
    """Fetch a card's detail page for description, date, seniority, full skills.

    Returns the raw dict enriched in place (or unchanged if the detail page
    cannot be fetched -- missing fields stay None rather than raising).
    """
    detail_url = raw.get("detail_url")
    if not detail_url:
        return raw
    try:
        resp = client.get(str(detail_url), timeout=get_run_config().http_timeout)
        resp.raise_for_status()
    except httpx.HTTPError:
        return raw
    page = BeautifulSoup(resp.text, "html.parser")

    # JSON-LD JobPosting: datePosted, employmentType, hiringOrganization,
    # structured jobLocation
    ld = {}
    for script in page.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            ld = data
            break
    if ld.get("datePosted"):
        raw["date_posted"] = ld["datePosted"]
    if not raw.get("company") and ld.get("hiringOrganization"):
        org = ld.get("hiringOrganization") or {}
        raw["company"] = org.get("name")
    addr = (ld.get("jobLocation") or {}).get("address") or {}
    locality = addr.get("addressLocality")
    region = addr.get("addressRegion")
    country = addr.get("addressCountry")
    if locality or region or country:
        raw["location"] = html.unescape(", ".join(p for p in (locality, region, country) if p))

    # Full description
    desc_el = page.select_one("div.content-single")
    if desc_el:
        desc = desc_el.get_text(" ", strip=True)
        desc = re.sub(r"^Job Description\s*", "", desc)
        raw["description"] = desc

    # Full skill list (card only shows first N + "+N more")
    skills_el = page.select_one("h6.mb-15")
    if skills_el and "required skills" in skills_el.get_text(" ", strip=True).lower():
        parent = skills_el.parent
        full_skills = [
            b.get_text(" ", strip=True)
            for b in parent.select("a.badge[href^='/skills/']")
            if b.get_text(" ", strip=True)
        ]
        if full_skills:
            raw["skills"] = full_skills

    # Job level -> seniority
    for el in page.select("div.sidebar-text-info"):
        text = el.get_text(" ", strip=True)
        if text.lower().startswith("job level"):
            raw["job_level"] = text.split("Job level", 1)[-1].strip()
            break
    return raw


def normalize_job(raw: dict[str, object]) -> CanonicalJob:
    """Convert a raw datasciencejobs card into a CanonicalJob."""
    job = new_canonical_job("datasciencejobs")
    contract_raw = raw.get("contract_raw")
    contract_types = (
        [CONTRACT_NORM_MAP[contract_raw].value]  # type: ignore[index]
        if contract_raw in CONTRACT_NORM_MAP
        else []
    )
    job_level = (raw.get("job_level") or "").lower()
    salary = parse_salary(raw.get("salary_text"))
    job.update({
        "id": raw.get("id") or "",
        "source_url": raw.get("detail_url"),
        "title": raw.get("title") or "",
        "company": raw.get("company") or "",
        "apply_url": raw.get("apply_url"),
        "location_raw": raw.get("location") or "",
        "workplace_type": detect_workplace(raw.get("location") or ""),
        "date_posted": raw.get("date_posted"),
        "salary": salary,
        "contract_types": contract_types,
        "seniority_level": SENIORITY_NORM_MAP.get(job_level),
        "technologies": raw.get("skills") or [],
        "description_text": raw.get("description") or "",
        "description_language": "en",
        "engagement_type": EngagementType.DIRECT,
        "_source": raw,
    })
    if raw.get("company"):
        job["company_info"]["name"] = str(raw["company"])
    return job


def find_page_count(soup: BeautifulSoup) -> int:
    """Extract total pages from /jobs/page/N/ pagination links."""
    max_page = 1
    for a in soup.select("a.page-number[href]"):
        match = re.search(r"/jobs/page/(\d+)/", a.get("href", ""))
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


def _write_output(output: Path, fmt: str, all_jobs: list[CanonicalJob]) -> None:
    """Write the jobs array/CSV, truncating any stale partial from a prior run.

    Opens with ``"w"`` (never append) so a fresh run never mixes with prior
    output. JSON keeps the canonical top-level array schema (silver readers
    depend on it).
    """
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


def scrape(list_url: str, output: Path, max_pages: int | None, fmt: str,
           query: str) -> int:
    """Core scraping logic. Returns number of jobs scraped.

    Resilient to mid-board failures: a single job's ``fetch_detail`` failure
    skips just that job, and a page failure breaks the loop while keeping every
    prior page. Whatever completed is written out before returning, so a
    partial run is ingested rather than lost.
    """
    client = httpx.Client(headers=HEADERS, follow_redirects=True)

    all_jobs: list[CanonicalJob] = []
    seen_ids: set[str] = set()

    try:
        html = fetch_page(client, list_url)
    except Exception as exc:
        print(f"  Failed to load first page: {exc}")
        html = ""
    soup = BeautifulSoup(html, "html.parser")
    total_pages = find_page_count(soup)
    if not html.strip():
        # First page failed to load -> clean partial of 0 pages, not a crash.
        total_pages = 0
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)
    print(f"Found {total_pages} pages")

    for page in range(1, total_pages + 1):
        try:
            if page > 1:
                url = f"{BASE_URL}/jobs/page/{page}/"
                html = fetch_page(client, url)
                soup = BeautifulSoup(html, "html.parser")

            page_jobs = 0
            for card in soup.select("div.card-grid-2"):
                raw = extract_job(card)
                if not raw or not raw.get("title"):
                    continue
                if not title_matches(str(raw["title"]), query):
                    continue
                job_id = raw.get("id")
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)  # type: ignore[arg-type]
                try:
                    raw = fetch_detail(client, raw)
                except Exception as exc:
                    print(f"    Skipping job {job_id}: {exc}")
                    continue
                all_jobs.append(normalize_job(raw))
                page_jobs += 1

            print(f"  Page {page}: {page_jobs} jobs")

            # Write-what-you-have after each completed page so even a hard
            # process kill keeps the last fully-written page.
            _write_output(output, fmt, all_jobs)
        except Exception as exc:
            print(f"  Page {page} failed: {exc}; keeping {len(all_jobs)} jobs from prior pages")
            break

    client.close()

    # Final write reflects every completed page. When a mid-board failure broke
    # the loop, this persists pages 1..N-1; on an empty/failed first page it
    # persists ``[]``.
    _write_output(output, fmt, all_jobs)

    print(f"\nWrote {len(all_jobs)} jobs to {output}")
    return len(all_jobs)


@app.command("datasciencejobs")
def datasciencejobs(
    query: Annotated[
        str, typer.Option("--query", "-q", help="Job title / keyword search (post-filter: no server-side search)")
    ] = DEFAULT_QUERY,
    location: Annotated[
        str, typer.Option("--location", "-l", help="Location (ignored: no server-side location filter on datasciencejobs.com)")
    ] = DEFAULT_LOCATION,
    contracts: Annotated[
        Optional[list[str]],
        typer.Option("--contracts", "-c", help="Contract types (ignored: no contract filter on datasciencejobs.com)"),
    ] = None,
    remote: Annotated[
        Optional[list[str]],
        typer.Option("--remote", "-r", help="Remote types (ignored: no remote filter on datasciencejobs.com)"),
    ] = None,
    experience: Annotated[
        Optional[list[str]],
        typer.Option("--experience", "-e", help="Experience levels (ignored: no experience filter on datasciencejobs.com)"),
    ] = None,
    sort: Annotated[
        Optional[str],
        typer.Option("--sort", "-s", help="Sort order (ignored: board sorts by recency)"),
    ] = None,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output path")
    ] = Path("datasciencejobs_jobs.json"),
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
    """Scrape job listings from datasciencejobs.com.

    Defaults to data jobs (--query acts as a client-side post-filter on
    titles -- the board has no server-side search). The board has no
    contract/remote/experience filters either; those flags are accepted for
    CLI parity with the other scrapers but ignored here.
    """
    for name, value in (("contracts", contracts), ("remote", remote),
                        ("experience", experience), ("sort", sort),
                        ("location", location)):
        if value:
            print(f"Note: --{name} is not supported by datasciencejobs.com -- ignored")

    if fmt == "csv" and output == Path("datasciencejobs_jobs.json"):
        output = Path("datasciencejobs_jobs.csv")

    if url:
        list_url = url
        print(f"Using URL: {list_url}")
    else:
        list_url = build_url(query, location)
        print(f"Search URL: {list_url}")

    scrape(list_url, output, max_pages, fmt, query)


if __name__ == "__main__":
    app()
