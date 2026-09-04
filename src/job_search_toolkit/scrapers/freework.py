"""
Scrape tech/IT job listings from free-work.com.

Server-rendered HTML -- no JS/Playwright needed. Paginates via ?page=N.
Output: CSV with title, company, contract types, skills, date, description,
         start date, duration, pay, daily rate, remote type, location, URL.

Usage:
    uv run python scrape_freework.py
    uv run python scrape_freework.py --query "python developer" --contracts contractor
    uv run python scrape_freework.py --url "https://www.free-work.com/en-gb/tech-it/jobs?query=devops&..."
"""
import csv
import json
import re
from pathlib import Path
from typing import Annotated, Literal, Optional
from urllib.parse import urljoin, quote_plus

import httpx
import typer
from bs4 import BeautifulSoup, Tag

from job_search_toolkit.run_config import get_run_config
from job_search_toolkit.scrapers.http_retry import request_with_retry

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://www.free-work.com"
DEFAULT_LOCATIONS = ["fr~ile-de-france~paris~"]
DEFAULT_QUERY = "data engineer"
DEFAULT_CONTRACTS = ["contractor", "fixed-term", "permanent"]
DEFAULT_REMOTE = ["partial", "full", "none"]
DEFAULT_EXPERIENCE = ["senior", "intermediate", "junior"]
DEFAULT_SORT = "date"
DEFAULT_RADIUS = get_run_config().freework_radius

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
DETAILS_FIELDS = {
    "Start date": "start_date",
    "Duration": "duration",
    "Pay": "pay",
    "Rate": "rate",
    "Remote type": "remote_type",
    "Location": "location",
}


def build_url(
    query: str,
    locations: list[str],
    contracts: list[str],
    remote: list[str],
    experience: list[str],
    sort: str,
    radius: int,
) -> str:
    """Build the search URL from parameters."""
    params: list[str] = []
    for loc in locations:
        params.append(f"locations={loc}")
    params.append(f"query={quote_plus(query)}")
    for c in contracts:
        params.append(f"contracts={c}")
    for r in remote:
        params.append(f"remote={r}")
    for e in experience:
        params.append(f"experience={e}")
    params.append(f"sort={sort}")
    params.append(f"radius={radius}")
    return f"{BASE_URL}/en-gb/tech-it/jobs?{'&'.join(params)}"


def fetch_page(client: httpx.Client, list_url: str, page: int) -> str:
    url = f"{list_url}&page={page}"
    resp = request_with_retry(client, "GET", url, timeout=get_run_config().http_timeout)
    resp.raise_for_status()
    return resp.text


def parse_details(raw: str) -> dict[str, str]:
    """Parse the condensed details string.

    Raw text from get_text() glues labels to values with no separators:
    'Start dateAs soon as possibleDuration1 yearRate400-550 EUR ...'
    Strategy: match each known label and capture text until the next label.
    """
    cleaned = re.sub(r"SVG\s*Image\s*", " ", raw)
    labels_pattern = "|".join(re.escape(k) for k in DETAILS_FIELDS)
    pattern = re.compile(
        rf"({'|'.join(re.escape(k) for k in DETAILS_FIELDS)})\s*(.+?)(?=\s*(?:{labels_pattern})|$)",
    )
    result: dict[str, str] = {}
    for match in pattern.finditer(cleaned):
        label = match.group(1)
        value = match.group(2).strip()
        if value:
            result[label] = value
    return {DETAILS_FIELDS.get(k, k): v for k, v in result.items()}


def parse_contract_badges(contract_div: Tag) -> list[str]:
    """Extract unique contract types from the badge container."""
    seen: set[str] = set()
    badges: list[str] = []
    for child in contract_div.descendants:
        if isinstance(child, Tag) and child.name in ("span", "a", "div"):
            text = child.get_text(strip=True)
            if text.lower() in ("contractor", "permanent", "fixed term", "fixed-term"):
                canonical = text.strip().title()
                if canonical.lower() == "fixed-term":
                    canonical = "Fixed term"
                if canonical not in seen:
                    seen.add(canonical)
                    badges.append(canonical)
    return badges


def extract_job(outer_card: Tag) -> dict[str, object] | None:
    """Extract all fields from a job card container."""
    card_div = outer_card.find("div", class_=lambda c: c and "p-4" in c)
    if card_div is None:
        return None

    job: dict[str, object] = {}

    # Contract types
    contract_div = card_div.find("div")
    if contract_div:
        job["contract_types"] = parse_contract_badges(contract_div)

    # Title + URL
    title_a = card_div.find("a", href=lambda h: h and "/job-mission/" in h)
    if not title_a:
        return None
    job["title"] = title_a.get_text(strip=True)
    job["url"] = urljoin(BASE_URL, title_a.get("href", ""))

    # Skills and date
    skills_div = card_div.find("div", class_=lambda c: c and "-mt-3" in str(c))
    if skills_div:
        raw_skills: list[str] = []
        date_posted: str | None = None
        for t in skills_div.stripped_strings:
            if DATE_RE.match(t):
                date_posted = t
            elif not re.match(r"^\+\d+$", t):
                raw_skills.append(t)
        seen_skills: set[str] = set()
        skills: list[str] = []
        for s in raw_skills:
            if s not in seen_skills:
                seen_skills.add(s)
                skills.append(s)
        job["skills"] = skills
        job["date_posted"] = date_posted

    # Company + description
    info_div = card_div.find("div", class_=lambda c: c and "gap-4" in str(c))
    if info_div:
        img = info_div.find("img")
        if img:
            job["company_logo"] = urljoin(BASE_URL, img.get("src", ""))
        texts = list(info_div.stripped_strings)
        if texts:
            job["company"] = texts[0]
            desc_parts: list[str] = []
            for t in texts[1:]:
                if t.strip() in ("View this job", ""):
                    break
                desc_parts.append(t)
            job["description"] = " ".join(desc_parts)

    # Details
    for link in outer_card.find_all("a", href=lambda h: h and "/job-mission/" in h):
        text = link.get_text(strip=True)
        if "Start date" in text:
            job.update(parse_details(text))
            break

    return job


def find_page_count(soup: BeautifulSoup) -> int:
    """Extract total pages from '1 / 8' pagination text."""
    text = soup.get_text()
    for match in re.finditer(r"(\d+)\s*/\s*(\d+)", text):
        _current, total = int(match.group(1)), int(match.group(2))
        if total > 1:
            return total
    return 1
def scrape(list_url: str, output: Path, max_pages: int | None, fmt: str) -> int:
    """Core scraping logic. Returns number of jobs scraped."""
    client = httpx.Client(headers=HEADERS, follow_redirects=True)

    html = fetch_page(client, list_url, 1)
    soup = BeautifulSoup(html, "html.parser")
    total_pages = find_page_count(soup)
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)
    print(f"Found {total_pages} pages")

    all_jobs: list[dict[str, object]] = []

    for page in range(1, total_pages + 1):
        if page > 1:
            html = fetch_page(client, list_url, page)
            soup = BeautifulSoup(html, "html.parser")

        h2s = soup.find_all("h2")
        page_jobs = 0
        for h2 in h2s:
            outer_card = h2
            for _ in range(6):
                outer_card = outer_card.parent
                if outer_card is None:
                    break
                classes = " ".join(outer_card.get("class", []))
                if "rounded-lg" in classes and "shadow" in classes:
                    break
            if outer_card is None:
                continue
            job = extract_job(outer_card)
            if job and job.get("title"):
                all_jobs.append(job)
                page_jobs += 1

        print(f"  Page {page}: {page_jobs} jobs")

    client.close()

    if fmt == "json":
        # Write structured JSON — lists stay as arrays, None stays as null
        serializable: list[dict[str, object]] = []
        for job in all_jobs:
            clean: dict[str, object] = {}
            for k, v in job.items():
                if v is None:
                    clean[k] = None
                elif isinstance(v, list):
                    clean[k] = v
                else:
                    clean[k] = str(v)
            serializable.append(clean)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    else:
        fieldnames = [
            "title", "url", "company", "company_logo",
            "contract_types", "skills", "date_posted",
            "start_date", "duration", "pay", "rate",
            "remote_type", "location", "description",
        ]
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for job in all_jobs:
                row: dict[str, str] = {}
                for key in fieldnames:
                    val = job.get(key, "")
                    if isinstance(val, list):
                        val = " | ".join(str(v) for v in val)
                    row[key] = str(val) if val is not None else ""
                writer.writerow(row)

    print(f"\nWrote {len(all_jobs)} jobs to {output}")
    return len(all_jobs)


@app.command("freework")
def freework(
    query: Annotated[
        str, typer.Option("--query", "-q", help="Job title / keyword search")
    ] = DEFAULT_QUERY,
    locations: Annotated[
        list[str],
        typer.Option(
            "--locations", "-l",
            help="Location filter (format: country~region~city~). Repeatable.",
        ),
    ] = DEFAULT_LOCATIONS,
    contracts: Annotated[
        list[str],
        typer.Option(
            "--contracts", "-c",
            help="Contract types: contractor, permanent, fixed-term. Repeatable.",
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
        str, typer.Option("--sort", "-s", help="Sort order: date, relevance")
    ] = DEFAULT_SORT,
    radius: Annotated[
        int, typer.Option("--radius", help="Search radius in miles")
    ] = DEFAULT_RADIUS,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output CSV path")
    ] = Path("freework_jobs.csv"),
    url: Annotated[
        Optional[str],
        typer.Option(
            "--url", "-u",
            help="Full search URL from browser (overrides all other params)",
        ),
    ] = None,
    max_pages: Annotated[
        Optional[int],
        typer.Option("--max-pages", "-p", help="Limit to N pages"),
    ] = None,
    fmt: Annotated[
        Literal["csv", "json"],
        typer.Option("--format", "-f", help="Output format"),
    ] = "csv",
) -> None:
    """Scrape job listings from free-work.com.

    Defaults to Paris-area data engineer jobs across all contract types
    and experience levels. Use --url to paste a search URL from the browser.
    """
    if fmt == "json" and output == Path("freework_jobs.csv"):
        output = Path("freework_jobs.json")

    if url:
        list_url = url
        print(f"Using URL: {list_url}")
    else:
        list_url = build_url(query, locations, contracts, remote, experience, sort, radius)
        print(f"Search URL: {list_url}")

    scrape(list_url, output, max_pages, fmt)


if __name__ == "__main__":
    app()
