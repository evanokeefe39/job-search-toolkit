"""Scrape English-speaking job listings in Europe from faruse.com.

Live-probe finding (2026-08-11): the faruse frontend is a JavaScript SPA
(v2.faruse.com) — every HTML route returns the same empty app shell, so there
is no server-rendered job markup to parse. The site's data layer, however, is a
public JSON API backed by Supabase: an anonymous (anon-role) key is shipped
inside the client bundle, the `jobs` table is RLS-open to anonymous reads, and
robots.txt allows all crawlers. This scraper uses that public API directly with
plain httpx — the same pattern the project already uses for Remote OK — with
no JS execution and no new dependencies.

Primary path: POST the `search-jobs` edge function (what the site's own
frontend calls). Fallback: direct REST query on the `jobs` table (the app's own
fallback path) with token post-filtering, when the edge function is unavailable.

Pagination: 1-based `page` param, `pageSize=50`, `pagination.hasMore` flag.

Usage:
    uv run python -m job_search_toolkit.scrapers.faruse
    uv run python -m job_search_toolkit.scrapers.faruse --query "python developer" --location france
    uv run python -m job_search_toolkit.scrapers.faruse --format json --max-pages 1
"""
import csv
import json
import logging
import re
from pathlib import Path
from typing import Annotated, Literal, Optional
from urllib.parse import quote

import httpx
import typer

from job_search_toolkit.run_config import get_run_config
from job_search_toolkit.schemas import (
    CanonicalJob,
    CompanyInfo,
    CompanyType,
    ContractType,
    EnrichmentStatus,
    EngagementType,
    RoleCategory,
    Salary,
    SeniorityLevel,
    WorkplaceType,
    new_canonical_job,
)

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://www.faruse.com"
SUPABASE_URL = "https://lqvujiotzwdxjeexkzxm.supabase.co"
SEARCH_FUNCTION_URL = f"{SUPABASE_URL}/functions/v1/search-jobs"
JOBS_TABLE_URL = f"{SUPABASE_URL}/rest/v1/jobs"

# Public anon-role key shipped in the client bundle (RLS allows anon reads of
# the jobs table — this is the same read path the site's own frontend uses).
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxxdnVqaW90"
    "endkeGplZXhrenhtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjI1ODk4OTYsImV4cCI6MjA3ODE2NTg5"
    "Nn0.Yr42S073XMeZ1gNJ4_q-Avv5rckmO0897a-Ys2kE5hQ"
)

DEFAULT_QUERY = "data engineer"
DEFAULT_LOCATION = "france"
DEFAULT_CONTRACTS: list[str] = []
DEFAULT_REMOTE: list[str] = []
DEFAULT_EXPERIENCE: list[str] = []
DEFAULT_SORT = ""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "apikey": ANON_KEY,
    "Authorization": f"Bearer {ANON_KEY}",
}

logger = logging.getLogger(__name__)

# Fixed EUR/USD rate for salary normalization (same convention as hiringcafe)
EUR_USD_RATE = 0.92

# Common European country names for the site's `country` filter. The edge
# function matches substrings on full country names (e.g. "France"), so map
# free-form location input onto canonical country names where possible.
COUNTRY_ALIASES = {
    "france": "France",
    "germany": "Germany",
    "netherlands": "Netherlands",
    "spain": "Spain",
    "italy": "Italy",
    "portugal": "Portugal",
    "austria": "Austria",
    "belgium": "Belgium",
    "ireland": "Ireland",
    "poland": "Poland",
    "sweden": "Sweden",
    "denmark": "Denmark",
    "norway": "Norway",
    "finland": "Finland",
    "czechia": "Czechia",
    "czech republic": "Czechia",
    "switzerland": "Switzerland",
    "croatia": "Croatia",
    "hungary": "Hungary",
    "romania": "Romania",
    "greece": "Greece",
    "slovakia": "Slovakia",
    "slovenia": "Slovenia",
    "estonia": "Estonia",
    "latvia": "Latvia",
    "lithuania": "Lithuania",
    "luxembourg": "Luxembourg",
    "malta": "Malta",
    "cyprus": "Cyprus",
    "bulgaria": "Bulgaria",
    "iceland": "Iceland",
}

# CLI contract value -> site's contractType value
CONTRACT_MAP = {
    "full-time": "Full-time",
    "permanent": "Full-time",
    "contract": "Contract",
    "contractor": "Contract",
    "part-time": "Part-time",
    "internship": "Internship",
    "temporary": "Contract",
}

# CLI remote value -> site's Remote value ("none" = no filter on the API)
REMOTE_MAP = {
    "full": "Remote",
    "remote": "Remote",
    "partial": "Hybrid",
    "hybrid": "Hybrid",
    "none": None,
    "onsite": None,
}

# CLI experience value -> site's experienceLevel value
EXPERIENCE_MAP = {
    "junior": "Entry level",
    "entry": "Entry level",
    "intermediate": "Associate",
    "senior": "Mid-Senior level",
    "lead": "Director",
    "manager": "Director",
}

# site contractType -> ContractType enum
CONTRACT_TYPE_NORM = {
    "Full-time": ContractType.FULL_TIME,
    "Part-time": ContractType.PART_TIME,
    "Contract": ContractType.CONTRACT,
    "Internship": ContractType.INTERNSHIP,
    "Temporary": ContractType.TEMPORARY,
}

# site experienceLevel -> SeniorityLevel enum
SENIORITY_NORM = {
    "Entry level": SeniorityLevel.ENTRY,
    "Internship": SeniorityLevel.ENTRY,
    "Associate": SeniorityLevel.MID,
    "Mid-Senior level": SeniorityLevel.SENIOR,
    "Director": SeniorityLevel.MANAGER,
    "Executive": SeniorityLevel.MANAGER,
}

# site Remote value -> WorkplaceType enum
WORKPLACE_NORM = {
    "Remote": WorkplaceType.REMOTE,
    "Hybrid": WorkplaceType.HYBRID,
}

SALARY_RE = re.compile(
    r"^\s*(\d+[\d\s.,]*)\s*-\s*(\d+[\d\s.,]*)\s*([A-Za-z€$]{2,4})\s*/\s*(year|yr|yearly|month|mo|monthly|day|daily|hour|hr|hourly)\s*$",
    re.IGNORECASE,
)

FREQ_MULTIPLIER = {
    "year": 1, "yr": 1, "yearly": 1,
    "month": 12, "mo": 12, "monthly": 12,
    "day": 260, "daily": 260,
    "hour": 2080, "hr": 2080, "hourly": 2080,
}

CURRENCY_EUR_RATE = {
    "EUR": 1.0, "€": 1.0,
    "USD": EUR_USD_RATE,
    "US$": EUR_USD_RATE,
    "$": EUR_USD_RATE,
}

# Data-role keyword -> RoleCategory heuristic for the title
ROLE_CATEGORY_MAP: list[tuple[tuple[str, ...], RoleCategory]] = [
    (("data engineer", "data engineering", "big data engineer"), RoleCategory.DATA_ENGINEER),
    (("data scientist", "machine learning scientist"), RoleCategory.DATA_SCIENTIST),
    (("machine learning", "ml engineer", "deep learning", "ai engineer", "llm"), RoleCategory.ML_ENGINEER),
    (("analytics engineer", "business intelligence", "bi engineer"), RoleCategory.ANALYTICS_ENGINEER),
    (("data analyst", "analyst"), RoleCategory.DATA_ANALYST),
    (("platform engineer", "infrastructure engineer", "sre", "site reliability"), RoleCategory.PLATFORM_ENGINEER),
    (("devops", "cloud engineer"), RoleCategory.DEVOPS_ENGINEER),
    (("software engineer", "software developer", "backend", "frontend", "fullstack", "full-stack", "full stack",
      "developer", "programmer"), RoleCategory.SOFTWARE_ENGINEER),
    (("engineering manager", "tech lead", "head of engineering"), RoleCategory.ENGINEERING_MANAGER),
    (("product manager", "product owner"), RoleCategory.PRODUCT_MANAGER),
]


def build_url(
    query: str,
    location: str,
    contracts: list[str],
    remote: list[str],
    experience: list[str],
    sort: str,
) -> str:
    """Return the search-jobs endpoint URL (filters travel in the POST body).

    Kept for parity with the free-work scraper shape; the returned URL is the
    edge-function endpoint. `build_payload` carries the actual filter values.
    """
    params = [f"searchQuery={quote(query)}"]
    country = COUNTRY_ALIASES.get(location.strip().lower())
    if country:
        params.append(f"country={quote(country)}")
    elif location.strip():
        params.append(f"city={quote(location.strip())}")
    if sort:
        params.append(f"sort={quote(sort)}")
    return f"{SEARCH_FUNCTION_URL}?{'&'.join(params)}"


def build_payload(
    query: str,
    location: str,
    contracts: list[str],
    remote: list[str],
    experience: list[str],
    page: int,
) -> dict:
    """Build the search-jobs POST body from CLI parameters."""
    payload: dict = {
        "page": page,
        "pageSize": get_run_config().faruse_page_size,
        "searchQuery": query or "",
        "searchIn": "all",
    }
    country = COUNTRY_ALIASES.get(location.strip().lower())
    if country:
        payload["country"] = country
    elif location.strip():
        payload["city"] = location.strip()

    for c in contracts:
        site_val = CONTRACT_MAP.get(c.strip().lower())
        if site_val:
            payload["contractType"] = site_val
            break
    else:
        if contracts:
            logger.warning("No faruse contractType matches %s; ignoring filter", contracts)

    for r in remote:
        site_val = REMOTE_MAP.get(r.strip().lower())
        if site_val:
            payload["remote"] = site_val
            break
        if r.strip().lower() in ("none", "onsite"):
            break  # no corresponding filter value; drop the filter

    for e in experience:
        site_val = EXPERIENCE_MAP.get(e.strip().lower())
        if site_val:
            payload["experienceLevel"] = site_val
            break

    return payload


def fetch_page(client: httpx.Client, url: str, payload: dict) -> dict:
    """POST one page of the search-jobs edge function. Returns parsed JSON."""
    resp = client.post(url, json=payload, timeout=get_run_config().http_timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_page_rest(client: httpx.Client, page: int) -> list[dict]:
    """Direct REST query on the jobs table (the app's own fallback path).

    Returns the raw rows for one page, ordered by publishedAt descending.
    """
    resp = client.get(
        JOBS_TABLE_URL,
        params={
            "select": "*",
            "order": "publishedAt.desc",
            "offset": (page - 1) * get_run_config().faruse_page_size,
            "limit": get_run_config().faruse_page_size,
        },
        timeout=get_run_config().http_timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def rest_matches(record: dict, query: str, location: str) -> bool:
    """Post-filter a REST row to approximate search-jobs semantics.

    The edge function searches title+description for all query tokens and
    substring-matches the country; the plain REST query has no filters, so we
    apply the same matching here for fallback parity.
    """
    text = " ".join([
        str(record.get("title") or ""),
        str(record.get("description") or ""),
    ]).lower()
    for token in query.split():
        if token.lower() not in text:
            return False
    country = COUNTRY_ALIASES.get(location.strip().lower())
    if country:
        rec_country = str(record.get("country") or "")
        if country.lower() not in rec_country.lower():
            return False
    elif location.strip():
        rec_loc = str(record.get("location") or "").lower()
        if location.strip().lower() not in rec_loc:
            return False
    return True


APPLICATIONS_RE = re.compile(r"(\d+)")


def parse_applications(raw) -> int | None:
    """Extract the applicant count from faruse's descriptive string.

    The API reports e.g. 'Be among the first 25 applicants' or
    'Over 200 applicants'; the schema wants an int (or None).
    """
    if raw is None:
        return None
    m = APPLICATIONS_RE.search(str(raw))
    return int(m.group(1)) if m else None


def extract_job(record: dict) -> dict | None:
    """Extract the fields we care about from one API record."""
    title = str(record.get("title") or "").strip()
    if not title:
        return None
    job = {
        "id": record.get("jobId") or record.get("jobUrl") or "",
        "title": title,
        "company": record.get("companyName"),
        "location": record.get("location"),
        "country": record.get("country"),
        "region": record.get("region"),
        "description": record.get("description"),
        "url": record.get("jobUrl"),
        "apply_url": record.get("applyUrl") or record.get("jobUrl"),
        "published_at": record.get("publishedAt"),
        "posted_time": record.get("postedTime"),
        "salary_raw": record.get("salary"),
        "work_type": record.get("workType"),
        "contract_type": record.get("contractType"),
        "experience_level": record.get("experienceLevel"),
        "remote": record.get("Remote"),
        "language": record.get("Language"),
        "sector": record.get("sector"),
        "visa_sponsorship": record.get("Visa Sponsorship"),
        "benefits": record.get("benefits"),
        "applications": record.get("applicationsCount"),
    }
    return job


def parse_salary(raw: str | None) -> Salary:
    """Parse a faruse salary string like '5500-7200 USD/month' into Salary."""
    default = Salary(
        min_annual_eur=None,
        max_annual_eur=None,
        currency_original="EUR",
        frequency_original="yearly",
        is_disclosed=False,
    )
    if not raw:
        return default
    m = SALARY_RE.match(str(raw).strip())
    if not m:
        return default
    try:
        min_val = float(m.group(1).replace(" ", "").replace(",", "."))
        max_val = float(m.group(2).replace(" ", "").replace(",", "."))
    except ValueError:
        return default
    currency = m.group(3).upper()
    freq = m.group(4).lower()
    rate = CURRENCY_EUR_RATE.get(currency)
    if rate is None:
        return default
    multiplier = FREQ_MULTIPLIER.get(freq, 1)
    min_annual_eur = round(min_val * multiplier * rate)
    max_annual_eur = round(max_val * multiplier * rate)
    return Salary(
        min_annual_eur=min_annual_eur,
        max_annual_eur=max_annual_eur,
        currency_original=currency,
        frequency_original=freq,
        is_disclosed=True,
    )


def infer_role_category(title: str) -> RoleCategory:
    """Heuristic role category from the job title."""
    low = title.lower()
    for keywords, category in ROLE_CATEGORY_MAP:
        if any(k in low for k in keywords):
            return category
    return RoleCategory.OTHER


def normalize_job(raw: dict) -> CanonicalJob:
    """Convert a raw faruse record into a CanonicalJob."""
    job = new_canonical_job("faruse")

    title = str(raw.get("title") or "")
    job_id = str(raw.get("id") or "")
    if not job_id:
        # fall back to a stable slug from the URL
        url = str(raw.get("url") or "")
        job_id = url.rstrip("/").split("/")[-1] or url

    company_name = str(raw.get("company") or "").strip()
    location = str(raw.get("location") or "").strip()
    description = str(raw.get("description") or "").strip()
    if description.lower().startswith("description"):
        description = description[len("description"):].lstrip()

    country = str(raw.get("country") or "").strip() or None

    workplace = WORKPLACE_NORM.get(str(raw.get("remote") or ""))
    contract_raw = str(raw.get("contract_type") or "")
    contract_types = [CONTRACT_TYPE_NORM[contract_raw]] if contract_raw in CONTRACT_TYPE_NORM else []
    seniority = SENIORITY_NORM.get(str(raw.get("experience_level") or ""))

    salary = parse_salary(raw.get("salary_raw"))

    job.update({
        "id": job_id,
        "source_url": raw.get("url"),
        "title": title,
        "company": company_name,
        "apply_url": raw.get("apply_url"),
        "location_raw": location,
        "workplace_type": workplace,
        "date_posted": raw.get("published_at") or None,
        "salary": salary,
        "contract_types": contract_types,
        "seniority_level": seniority,
        "role_category": infer_role_category(title),
        "years_experience_min": None,
        "technologies": [],
        "competencies": [],
        "description_text": description,
        "description_language": "en",
        "company_info": CompanyInfo(
            name=company_name,
            industry=[],
            size_employees=None,
            year_founded=None,
            hq_country=country,
            org_type=CompanyType.UNKNOWN,
            stock_symbol=None,
            stock_exchange=None,
            latest_funding_type=None,
            latest_funding_amount_usd=None,
            homepage_url=raw.get("company_url"),
        ),
        "engagement_type": EngagementType.UNKNOWN,
        "posting_company_type": None,
        "end_client_name": None,
        "end_client_sector": raw.get("sector"),
        "contract_duration": None,
        "views": None,
        "applications": parse_applications(raw.get("applications")),
        "is_expired": False,
        "_enrichment": EnrichmentStatus(
            tech_extracted=False,
            company_researched=False,
            vertical_classified=False,
            translated=True,
            scored=False,
        ),
        "scores": None,
        "overall_score": None,
        "_source": dict(raw),
    })
    return job


def scrape(url: str, output: Path, max_pages: int | None, fmt: str, query: str, location: str) -> int:
    """Core scraping logic. Returns number of jobs scraped."""
    client = httpx.Client(headers=HEADERS, follow_redirects=True)

    all_jobs: list[CanonicalJob] = []
    seen_ids: set[str] = set()
    page = 1
    rest_fallback = False

    while True:
        payload = build_payload(query, location, [], [], [], page)
        try:
            data = fetch_page(client, url, payload)
            records = data.get("jobs") or []
            pagination = data.get("pagination") or {}
            total_pages = pagination.get("totalPages") or 1
            has_more = bool(pagination.get("hasMore"))
        except httpx.HTTPError as e:
            if rest_fallback:
                raise
            logger.warning("search-jobs edge function failed (%s); falling back to REST", e)
            rest_fallback = True

        if rest_fallback:
            records = fetch_page_rest(client, page)
            records = [r for r in records if rest_matches(r, query, location)]
            total_pages = page + 1  # unknown; iterate until an empty page
            has_more = len(records) >= get_run_config().faruse_page_size

        page_jobs = 0
        for record in records:
            raw = extract_job(record)
            if raw and raw.get("title"):
                normalized = normalize_job(raw)
                if normalized["id"] not in seen_ids:
                    seen_ids.add(normalized["id"])
                    all_jobs.append(normalized)
                    page_jobs += 1
        print(f"  Page {page}: {page_jobs} new jobs")

        if max_pages is not None and page >= max_pages:
            break
        if not has_more or page >= total_pages:
            break
        page += 1

    client.close()

    if fmt == "json":
        with open(output, "w", encoding="utf-8") as f:
            json.dump(all_jobs, f, ensure_ascii=False, indent=2)
    else:
        fieldnames = [
            "id", "title", "company", "location_raw", "date_posted",
            "workplace_type", "contract_types", "seniority_level", "role_category",
            "salary_min_annual_eur", "salary_max_annual_eur", "salary_currency_original",
            "apply_url", "source_url", "description_language",
        ]
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for job in all_jobs:
                row = {
                    "id": job["id"],
                    "title": job["title"],
                    "company": job["company"],
                    "location_raw": job["location_raw"],
                    "date_posted": job["date_posted"],
                    "workplace_type": job["workplace_type"].value if job["workplace_type"] else "",
                    "contract_types": " | ".join(c.value for c in job["contract_types"]),
                    "seniority_level": job["seniority_level"].value if job["seniority_level"] else "",
                    "role_category": job["role_category"].value if job["role_category"] else "",
                    "salary_min_annual_eur": job["salary"]["min_annual_eur"],
                    "salary_max_annual_eur": job["salary"]["max_annual_eur"],
                    "salary_currency_original": job["salary"]["currency_original"],
                    "apply_url": job["apply_url"],
                    "source_url": job["source_url"],
                    "description_language": job["description_language"],
                }
                writer.writerow(row)

    print(f"\nWrote {len(all_jobs)} jobs to {output}")
    return len(all_jobs)


@app.command("faruse")
def faruse(
    query: Annotated[
        str, typer.Option("--query", "-q", help="Job title / keyword search")
    ] = DEFAULT_QUERY,
    location: Annotated[
        str, typer.Option("--location", "-l", help="Country (e.g. france, germany) or city")
    ] = DEFAULT_LOCATION,
    contracts: Annotated[
        list[str],
        typer.Option(
            "--contracts", "-c",
            help="Contract types: full-time, part-time, contract, internship. Repeatable.",
        ),
    ] = DEFAULT_CONTRACTS,
    remote: Annotated[
        list[str],
        typer.Option(
            "--remote", "-r",
            help="Remote types: full, partial, none. Repeatable.",
        ),
    ] = DEFAULT_REMOTE,
    experience: Annotated[
        list[str],
        typer.Option(
            "--experience", "-e",
            help="Experience levels: junior, intermediate, senior. Repeatable.",
        ),
    ] = DEFAULT_EXPERIENCE,
    sort: Annotated[
        str, typer.Option("--sort", "-s", help="Sort order (ignored by faruse)")
    ] = DEFAULT_SORT,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output path")
    ] = Path("faruse_jobs.csv"),
    max_pages: Annotated[
        Optional[int],
        typer.Option("--max-pages", "-p", help="Limit to N pages"),
    ] = None,
    fmt: Annotated[
        Literal["csv", "json"],
        typer.Option("--format", "-f", help="Output format"),
    ] = "csv",
) -> None:
    """Scrape job listings from faruse.com (English-speaking jobs in Europe).

    faruse's frontend is a JS SPA; this scraper uses its public JSON API
    (Supabase anon key, robots-allowed), the same pattern as Remote OK.
    Defaults to data-engineer jobs in France.
    """
    if fmt == "json" and output == Path("faruse_jobs.csv"):
        output = Path("faruse_jobs.json")

    url = build_url(query, location, contracts, remote, experience, sort)
    print(f"Search URL: {url}")

    scrape(url, output, max_pages, fmt, query, location)


if __name__ == "__main__":
    app()
