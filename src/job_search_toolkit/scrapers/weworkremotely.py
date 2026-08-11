"""
Scrape remote job listings from We Work Remotely.

WWR is a remote-only job board. This scraper reads its RSS feeds with the
stdlib xml.etree.ElementTree parser (no extra dependencies).

Live-probe findings (2026-08-11, verified against real httpx fetches):

- Live feeds (HTTP 200, application/rss+xml):
    /categories/remote-programming-jobs.rss      (25 items)
    /categories/remote-devops-sysadmin-jobs.rss  (51 items)
    /remote-jobs.rss                              (100 items, all categories)
- The data/analytics category feeds (remote-data-science-jobs.rss,
  remote-data-analysis-jobs.rss, remote-data-jobs.rss,
  remote-analytics-jobs.rss) return HTTP 301 with NO Location header and an
  empty body -- dead links. Their categories are covered by the main
  /remote-jobs.rss feed instead.
- There is NO <company> element in any namespace (the docs' wwr:company is
  gone from the current feeds). The company name is the prefix of <title>:
  "Company: Job Title".
- <item> children seen: title, link, guid, description (HTML), pubDate,
  category, region, and on some feeds type, skills, country, state,
  expires_at. The description HTML may contain salary information.

RSS is category-based, not keyword-searchable: --query is a post-filter on
title + category + skills. The feeds are single-page, so --max-pages is
accepted for CLI parity but ignored (a note is logged).

Output: canonical CanonicalJob records as JSON or CSV.

Usage:
    uv run python -m job_search_toolkit.scrapers.weworkremotely
    uv run python -m job_search_toolkit.scrapers.weworkremotely --query "data engineer"
    uv run python -m job_search_toolkit.scrapers.weworkremotely --format json --max-pages 1
"""
import csv
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Annotated, Literal, Optional

import httpx
import typer
from bs4 import BeautifulSoup

from job_search_toolkit.schemas import (
    CanonicalJob,
    ContractType,
    RoleCategory,
    Salary,
    WorkplaceType,
    new_canonical_job,
)

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://weworkremotely.com"
# All live category feeds relevant to data/analytics/programming/devops.
# Data/analytics categories have no dedicated feed (dead 301 links) and are
# covered by the main feed. Items are deduplicated on link in scrape().
FEED_URLS = [
    f"{BASE_URL}/categories/remote-programming-jobs.rss",
    f"{BASE_URL}/categories/remote-devops-sysadmin-jobs.rss",
    f"{BASE_URL}/remote-jobs.rss",
]
DEFAULT_QUERY = ""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

# Rough currency -> annual EUR normalization (the schema stores EUR). WWR
# posts salaries in the original currency; these are the only two non-EUR
# currencies seen on the board.
USD_TO_EUR = 0.92
GBP_TO_EUR = 1.17

# <type> element values (devops/main feeds) -> ContractType
CONTRACT_TYPE_MAP: dict[str, ContractType] = {
    "full-time": ContractType.FULL_TIME,
    "part-time": ContractType.PART_TIME,
    "contract": ContractType.CONTRACT,
    "freelance": ContractType.CONTRACT,
    "internship": ContractType.INTERNSHIP,
    "volunteer": ContractType.VOLUNTEER,
}

# WWR <category> values -> RoleCategory (best effort; unknown -> None)
ROLE_CATEGORY_MAP: dict[str, RoleCategory] = {
    "full-stack programming": RoleCategory.SOFTWARE_ENGINEER,
    "back-end programming": RoleCategory.SOFTWARE_ENGINEER,
    "front-end programming": RoleCategory.SOFTWARE_ENGINEER,
    "devops and sysadmin": RoleCategory.DEVOPS_ENGINEER,
    "data science": RoleCategory.DATA_SCIENTIST,
    "data analysis": RoleCategory.DATA_ANALYST,
    "machine learning": RoleCategory.ML_ENGINEER,
    "product": RoleCategory.PRODUCT_MANAGER,
}

# Currency-prefixed amounts, optionally thousands-scaled: $80,000 / €60k / £50K
SALARY_RE = re.compile(r"(?P<cur>[$€£])\s?(?P<amt>\d[\d,]*)(?P<scale>[kK])?")
# If pay is expressed hourly we cannot normalize to annual EUR without
# guessing hours -- bail out to the undisclosed default instead.
HOURLY_RE = re.compile(r"\b(per\s*hour|hourly|/\s*hr)\b", re.IGNORECASE)


def default_salary() -> Salary:
    """Undisclosed salary, matching new_canonical_job()'s defaults."""
    return Salary(
        min_annual_eur=None,
        max_annual_eur=None,
        currency_original="EUR",
        frequency_original="yearly",
        is_disclosed=False,
    )


def build_url(query: str, location: Optional[str]) -> list[str]:
    """Return the RSS feed URLs to scrape.

    WWR's RSS is category-based: there is no ?q= search param, so query and
    location do not change the URLs (query is applied as a post-filter in
    scrape()).
    """
    return list(FEED_URLS)


def fetch_feed(client: httpx.Client, url: str) -> ET.Element:
    """Fetch an RSS feed and parse it into an ElementTree root."""
    resp = client.get(url, timeout=30)
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def parse_date(raw: str | None) -> str | None:
    """Parse an RFC 2822 pubDate into an ISO 8601 date (YYYY-MM-DD)."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return dt.date().isoformat()


def split_company_title(title: str) -> tuple[str | None, str]:
    """WWR titles are 'Company: Job Title' -- split on the first ': '."""
    if ": " in title:
        company, _, rest = title.partition(": ")
        if company.strip() and rest.strip():
            return company.strip(), rest.strip()
    return None, title.strip()


def slug_from_url(url: str | None) -> str:
    """Stable board-scoped id: the trailing slug of the job permalink."""
    if not url:
        return ""
    return url.rstrip("/").rsplit("/", 1)[-1]


def map_contract_type(raw: str | None) -> list[str]:
    """Map the feed's <type> element to ContractType values."""
    if not raw:
        return []
    ct = CONTRACT_TYPE_MAP.get(raw.strip().lower())
    return [ct.value] if ct else []


def map_role_category(raw: str | None) -> RoleCategory | None:
    """Map the WWR <category> string to a RoleCategory (best effort)."""
    if not raw:
        return None
    return ROLE_CATEGORY_MAP.get(raw.strip().lower())


def parse_salary(text: str) -> Salary:
    """Parse salary amounts from stripped description text -> annual EUR.

    Conservative by design: only yearly amounts are normalized. Hourly pay
    or no amounts -> the undisclosed default (never fabricated).
    """
    if not text or HOURLY_RE.search(text):
        return default_salary()
    amounts: list[float] = []
    for match in SALARY_RE.finditer(text):
        amount = float(match.group("amt").replace(",", ""))
        if match.group("scale"):
            amount *= 1000.0
        currency = match.group("cur")
        if currency == "$":
            amount *= USD_TO_EUR
        elif currency == "£":
            amount *= GBP_TO_EUR
        amounts.append(amount)
    if not amounts:
        return default_salary()
    salary = default_salary()
    salary["min_annual_eur"] = round(min(amounts))
    if len(amounts) >= 2:
        salary["max_annual_eur"] = round(max(amounts))
    salary["is_disclosed"] = True
    return salary


def extract_job(item: ET.Element) -> dict[str, object] | None:
    """Extract all fields from one RSS <item> element."""
    def child_text(tag: str) -> str | None:
        el = item.find(tag)
        if el is not None and el.text:
            return el.text.strip()
        return None

    title_raw = child_text("title")
    if not title_raw:
        return None
    company, title = split_company_title(title_raw)
    link = child_text("link") or child_text("guid")
    description = child_text("description")
    skills_raw = child_text("skills")
    return {
        "id": slug_from_url(link),
        "title": title,
        "company": company,
        "url": link,
        "description_html": description,
        "category": child_text("category"),
        "region": child_text("region"),
        "contract_types": map_contract_type(child_text("type")),
        "technologies": [s.strip() for s in (skills_raw or "").split(",") if s.strip()],
        "date_posted": parse_date(child_text("pubDate")),
        "expires_at": child_text("expires_at"),
        "raw_title": title_raw,
    }


def normalize_job(raw: dict[str, object]) -> CanonicalJob:
    """Convert a raw WWR feed item into a CanonicalJob."""
    description_html = raw.get("description_html")
    description_text = BeautifulSoup(description_html or "", "html.parser").get_text(" ", strip=True)

    job = new_canonical_job("wwr")
    job.update({
        "id": raw.get("id") or "",
        "source_url": raw.get("url"),
        "title": raw.get("title") or "",
        "company": raw.get("company") or "",
        "apply_url": raw.get("url"),
        "location_raw": raw.get("region") or "",
        "workplace_type": WorkplaceType.REMOTE,
        "date_posted": raw.get("date_posted"),
        "salary": parse_salary(description_text),
        "contract_types": raw.get("contract_types") or [],
        "role_category": map_role_category(raw.get("category")),
        "technologies": raw.get("technologies") or [],
        "description_text": description_text,
        "description_language": "en",
        "_source": raw,
    })
    if raw.get("company"):
        job["company_info"]["name"] = str(raw["company"])
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


def scrape(feed_urls: list[str], output: Path, fmt: str, query: str,
           max_pages: int | None) -> int:
    """Core scraping logic. Returns number of jobs scraped."""
    if max_pages is not None:
        print("Note: RSS feeds are single-page -- --max-pages ignored")

    client = httpx.Client(headers=HEADERS, follow_redirects=True)
    all_jobs: list[CanonicalJob] = []
    seen_links: set[str] = set()
    query_l = (query or "").strip().lower()

    for feed_url in feed_urls:
        try:
            root = fetch_feed(client, feed_url)
        except httpx.HTTPError as exc:
            print(f"  Warning: failed to fetch {feed_url}: {exc}")
            continue
        items = [el for el in root.iter() if el.tag.endswith("item")]
        feed_jobs = 0
        for item in items:
            raw = extract_job(item)
            if not raw or not raw.get("title"):
                continue
            link = raw.get("url")
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)
            # --query is a post-filter on title + category + skills
            haystack = " ".join(
                str(raw.get(k) or "") for k in ("title", "category", "technologies")
            ).lower()
            if query_l and query_l not in haystack:
                continue
            all_jobs.append(normalize_job(raw))
            feed_jobs += 1
        print(f"  {feed_url}: {feed_jobs} jobs")
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


@app.command("wwr")
def wwr(
    query: Annotated[
        str,
        typer.Option("--query", "-q",
                     help="Post-filter on title, category and skills (RSS is category-based)"),
    ] = DEFAULT_QUERY,
    location: Annotated[
        Optional[str],
        typer.Option("--location", "-l", help="Location (ignored: WWR feeds are not location-filterable)"),
    ] = None,
    contracts: Annotated[
        Optional[list[str]],
        typer.Option("--contracts", "-c", help="Contract types (ignored: no contract filter on WWR)"),
    ] = None,
    remote: Annotated[
        Optional[list[str]],
        typer.Option("--remote", "-r", help="Remote types (ignored: WWR is remote-only)"),
    ] = None,
    experience: Annotated[
        Optional[list[str]],
        typer.Option("--experience", "-e", help="Experience levels (ignored: no experience filter on WWR)"),
    ] = None,
    sort: Annotated[
        Optional[str],
        typer.Option("--sort", "-s", help="Sort order (ignored: feeds sort by recency)"),
    ] = None,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output path")
    ] = Path("wwr_jobs.json"),
    max_pages: Annotated[
        Optional[int],
        typer.Option("--max-pages", "-p", help="Limit to N pages (ignored: RSS is single-page)"),
    ] = None,
    fmt: Annotated[
        Literal["json", "csv"],
        typer.Option("--format", "-f", help="Output format"),
    ] = "json",
) -> None:
    """Scrape job listings from weworkremotely.com.

    Reads the Programming, DevOps/Sysadmin and main RSS feeds (data and
    analytics categories have no dedicated feed -- the main feed covers
    them). The board is remote-only; all jobs get workplace_type=remote.
    RSS is category-based, so --query post-filters results and the
    contract/remote/experience/sort flags are accepted for CLI parity with
    the other scrapers but ignored here.
    """
    for name, value in (("location", location), ("contracts", contracts),
                        ("remote", remote), ("experience", experience),
                        ("sort", sort)):
        if value:
            print(f"Note: --{name} is not supported by WWR feeds -- ignored")

    if fmt == "csv" and output == Path("wwr_jobs.json"):
        output = Path("wwr_jobs.csv")

    feed_urls = build_url(query, location)
    print(f"Fetching {len(feed_urls)} RSS feeds (query filter: {query!r})")
    scrape(feed_urls, output, fmt, query, max_pages)


if __name__ == "__main__":
    app()
