"""
Scrape job listings from hellowork.com.

France's biggest general job board. Server-rendered HTML -- no JS/Playwright
needed. Results are paginated via ?p=N (note: ?page=N is ignored), and each
card carries title, company, location, contract type, remote marker and
posted date; salary and contract duration appear as extra tags when present.

Output: CanonicalJob records (JSON or flattened CSV).

Usage:
    uv run python -m job_search_toolkit.scrapers.hellowork
    uv run python -m job_search_toolkit.scrapers.hellowork --query "python developer" --contracts contractor
    uv run python -m job_search_toolkit.scrapers.hellowork --format json --max-pages 1 -o data/_tmp_probe.json
"""
import csv
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Optional
from urllib.parse import quote_plus

import httpx
import typer
from bs4 import BeautifulSoup, Tag

from job_search_toolkit.schemas import (
    CanonicalJob,
    ContractType,
    Salary,
    WorkplaceType,
    new_canonical_job,
)

from job_search_toolkit.run_config import get_run_config

app = typer.Typer(no_args_is_help=False)

BASE_URL = "https://www.hellowork.com"
SEARCH_PATH = "/fr-fr/emploi/recherche.html"
DEFAULT_QUERY = "data engineer"
DEFAULT_LOCATION = "france"
DEFAULT_CONTRACTS = ["contractor", "fixed-term", "permanent"]
DEFAULT_SORT = "date"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

logger = logging.getLogger(__name__)

# CLI contract filter value -> HelloWork search `c` parameter
CONTRACT_FILTER_MAP = {
    "permanent": "CDI",
    "fixed-term": "CDD",
    "contractor": "Freelance",
    "temporary": "Travail_temp",      # intérim
    "internship": "Stage",
    "apprenticeship": "Alternance",
    "independent": "Independant",
    "civil-servant": "Fonctionnaire",
}

# HelloWork contract label (as shown on the card) -> canonical ContractType
CONTRACT_NORM_MAP = {
    "CDI": ContractType.FULL_TIME,            # contrat à durée indéterminée = permanent
    "CDD": ContractType.TEMPORARY,            # fixed-term
    "Travail_temp": ContractType.TEMPORARY,   # intérim
    "Intérim": ContractType.TEMPORARY,
    "Freelance": ContractType.CONTRACT,
    "Independant": ContractType.CONTRACT,
    "Indépendant": ContractType.CONTRACT,
    "Stage": ContractType.INTERNSHIP,
    "Alternance": ContractType.INTERNSHIP,    # work-study
}

ABS_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
REL_DATE_RE = re.compile(r"il y a\s+(\d+)\s*(heure|jour|semaine|mois|an)s?", re.IGNORECASE)
SALARY_RE = re.compile(r"(\d[\d\s\u202f\u00a0.]*)\s*€")
DURATION_RE = re.compile(r"(\d+)\s*(mois|an)", re.IGNORECASE)

# Remote markers on the card tag ("Télétravail partiel", "Télétravail complet", ...)
WORKPLACE_MAP = {
    "complet": WorkplaceType.REMOTE,
    "total": WorkplaceType.REMOTE,
    "100%": WorkplaceType.REMOTE,
    "partiel": WorkplaceType.HYBRID,
    "occasionnel": WorkplaceType.HYBRID,
    "hybride": WorkplaceType.HYBRID,
    "mixte": WorkplaceType.HYBRID,
}


def build_url(query: str, location: str, contracts: list[str]) -> str:
    """Build the search URL from parameters."""
    params = [f"k={quote_plus(query)}", f"l={quote_plus(location)}"]
    for c in contracts:
        mapped = CONTRACT_FILTER_MAP.get(c)
        if mapped:
            params.append(f"c={mapped}")
    return f"{BASE_URL}{SEARCH_PATH}?{'&'.join(params)}"


def fetch_page(client: httpx.Client, list_url: str, page: int) -> str:
    """Fetch one results page (paginated via ?p=N)."""
    url = f"{list_url}&p={page}"
    resp = client.get(url, timeout=get_run_config().http_timeout)
    resp.raise_for_status()
    return resp.text


def find_page_count(soup: BeautifulSoup) -> int:
    """Extract total pages from the pagination buttons (name="p", value=N)."""
    pages: set[int] = set()
    for btn in soup.find_all("button", attrs={"name": "p"}):
        value = btn.get("value")
        if value and str(value).isdigit():
            pages.add(int(value))
    if pages:
        return max(pages)
    # Fallback: mobile pagination "sur 35"
    m = re.search(r"sur\s+(\d+)", soup.get_text())
    if m:
        return int(m.group(1))
    return 1


def parse_posted_date(text: str) -> str | None:
    """Convert a card date string into an ISO date (YYYY-MM-DD).

    Cards use relative French dates ("il y a 2 jours", "il y a 7 heures",
    "Aujourd'hui", "Hier") or an absolute DD/MM/YYYY.
    """
    text = text.strip()
    if not text:
        return None
    m = ABS_DATE_RE.search(text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    lowered = text.lower()
    if "aujourd" in lowered:
        return date.today().isoformat()
    if lowered == "hier" or lowered.startswith("hier"):
        return (date.today() - timedelta(days=1)).isoformat()
    m = REL_DATE_RE.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "heure":
            return (datetime.now() - timedelta(hours=n)).date().isoformat()
        if unit == "jour":
            return (date.today() - timedelta(days=n)).isoformat()
        if unit == "semaine":
            return (date.today() - timedelta(weeks=n)).isoformat()
        if unit == "mois":
            return (date.today() - timedelta(days=30 * n)).isoformat()
        if unit == "an":
            return (date.today() - timedelta(days=365 * n)).isoformat()
    return None


def parse_french_number(raw: str) -> float | None:
    """Parse a French-formatted number (spaces / narrow no-break spaces)."""
    cleaned = re.sub(r"[\s\u202f\u00a0]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_salary(text: str) -> Salary | None:
    """Parse a salary tag like '45 000 € - 60 000 €' into a Salary record."""
    matches = SALARY_RE.findall(text)
    if not matches:
        return None
    values = [v for v in (parse_french_number(m) for m in matches) if v is not None]
    if not values:
        return None
    lowered = text.lower()
    if "/h" in lowered or "par heure" in lowered or "horaire" in lowered:
        frequency = "hourly"
    elif "/mois" in lowered or "mensuel" in lowered:
        frequency = "monthly"
    else:
        frequency = "yearly"
    return Salary(
        min_annual_eur=min(values),
        max_annual_eur=max(values),
        currency_original="EUR",
        frequency_original=frequency,
        is_disclosed=True,
    )


def parse_workplace_type(tag_text: str) -> WorkplaceType | None:
    """Map a card tag ("Télétravail partiel", ...) to a WorkplaceType."""
    lowered = tag_text.lower()
    if "télétravail" not in lowered and "remote" not in lowered and "distanciel" not in lowered:
        return None
    for marker, wp in WORKPLACE_MAP.items():
        if marker in lowered:
            return wp
    return None


def extract_job(card: Tag) -> dict[str, object] | None:
    """Extract all fields from one job card (<li>)."""
    job: dict[str, object] = {}

    # --- Identity ---
    offer_id = card.get("data-id-storage-item-id") or card.get("data-hide-offer-item-id-value")
    if not offer_id:
        return None
    job["id"] = offer_id

    # --- Title, company, URL ---
    title_a = card.find("a", attrs={"data-cy": "offerTitle"})
    if title_a is None:
        title_a = card.find("a", href=lambda h: h and "/emplois/" in h)
    if title_a is None:
        return None
    href = title_a.get("href", "")
    job["url"] = href if href.startswith("http") else f"{BASE_URL}{href}"
    h3 = title_a.find("h3")
    if h3 is not None:
        paragraphs = h3.find_all("p")
        if paragraphs:
            job["title"] = paragraphs[0].get_text(strip=True)
            if len(paragraphs) > 1:
                job["company"] = paragraphs[-1].get_text(strip=True)
    if not job.get("title"):
        job["title"] = title_a.get_text(strip=True) or title_a.get("title", "")
    if not job.get("company"):
        job["company"] = ""

    # --- Tags: location, contract, remote/salary/duration ---
    loc_el = card.find(attrs={"data-cy": "localisationCard"})
    if loc_el is not None:
        job["location"] = loc_el.get_text(strip=True)
    else:
        job["location"] = None

    contract_el = card.find(attrs={"data-cy": "contractCard"})
    job["contract"] = contract_el.get_text(strip=True) if contract_el is not None else None

    extra_tags = [
        t.get_text(strip=True) for t in card.find_all(attrs={"data-cy": "contractTag"})
        if t.get_text(strip=True)
    ]
    job["extra_tags"] = extra_tags
    job["workplace_type"] = next(
        (parse_workplace_type(t) for t in extra_tags if parse_workplace_type(t) is not None),
        None,
    )

    salary = next((parse_salary(t) for t in extra_tags if parse_salary(t) is not None), None)
    job["salary"] = salary
    duration = None
    for t in extra_tags:
        m = DURATION_RE.search(t)
        if m:
            duration = f"{m.group(1)} {m.group(2).lower()}"
            break
    job["contract_duration"] = duration

    # --- Posted date ---
    date_div = card.find("div", class_=lambda c: c and "text-grey-500" in str(c) and "typo-s" in str(c))
    card_text = date_div.get_text(" ", strip=True) if date_div is not None else card.get_text(" ", strip=True)
    job["posted_date_raw"] = date_div.get_text(strip=True) if date_div is not None else None
    job["date_posted"] = parse_posted_date(card_text)

    return job


def fetch_job_description(client: httpx.Client, url: str) -> str | None:
    """Fetch a job's detail page and return its JSON-LD description.

    HelloWork search cards carry no description text (only title, company,
    location, contract, salary, date), so each job's detail page is fetched
    and the application/ld+json JobPosting block parsed for the full
    description. Returns clean text (HTML stripped) or None when the block
    is absent.
    """
    try:
        resp = client.get(url, timeout=get_run_config().http_timeout)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    detail_soup = BeautifulSoup(resp.text, "html.parser")
    for script in detail_soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "null")
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                description = item.get("description")
                if description:
                    return BeautifulSoup(str(description), "html.parser").get_text(" ", strip=True)
    return None


def normalize_job(raw: dict) -> CanonicalJob:
    """Convert a raw HelloWork card record into a CanonicalJob."""
    job = new_canonical_job("hellowork")
    job["id"] = str(raw.get("id") or "")
    job["source_url"] = raw.get("url")
    job["apply_url"] = raw.get("url")
    job["title"] = str(raw.get("title") or "")
    job["company"] = str(raw.get("company") or "")
    job["location_raw"] = str(raw.get("location") or "")
    job["company_info"]["name"] = job["company"]
    job["workplace_type"] = raw.get("workplace_type")
    job["date_posted"] = raw.get("date_posted")
    job["salary"] = raw.get("salary") or Salary(
        min_annual_eur=None, max_annual_eur=None,
        currency_original="EUR", frequency_original="yearly", is_disclosed=False,
    )
    contract_label = raw.get("contract")
    if contract_label:
        job["contract_types"] = [
            CONTRACT_NORM_MAP.get(str(contract_label), ContractType.FULL_TIME)
        ]
    job["contract_duration"] = raw.get("contract_duration")
    job["description_text"] = raw.get("description")
    job["description_language"] = "fr"
    job["_source"] = raw
    return job


def flatten_canonical(job: CanonicalJob) -> dict:
    """Flatten a CanonicalJob into a CSV-friendly flat dict."""
    s = job.get("salary") or {}
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
        "description_language": job.get("description_language"),
        "is_expired": job.get("is_expired"),
    }


def export_json(jobs: list[CanonicalJob], path: Path) -> None:
    path.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Exported %d jobs to %s", len(jobs), path)


def export_csv(jobs: list[CanonicalJob], path: Path) -> None:
    if not jobs:
        logger.warning("No jobs to export.")
        return
    flat = [flatten_canonical(j) for j in jobs]
    fieldnames = list(flat[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat)
    logger.info("Exported %d jobs to %s", len(jobs), path)


def scrape(list_url: str, output: Path, max_pages: int | None, fmt: str) -> int:
    """Core scraping logic. Returns number of jobs scraped."""
    client = httpx.Client(headers=HEADERS, follow_redirects=True)
    try:
        html = fetch_page(client, list_url, 1)
        soup = BeautifulSoup(html, "html.parser")
        total_pages = find_page_count(soup)
        if max_pages is not None:
            total_pages = min(total_pages, max_pages)
        print(f"Found {total_pages} pages")

        all_jobs: list[CanonicalJob] = []
        for page in range(1, total_pages + 1):
            if page > 1:
                html = fetch_page(client, list_url, page)
                soup = BeautifulSoup(html, "html.parser")
            cards = soup.find_all("li", attrs={"data-id-storage-item-id": True})
            page_jobs = 0
            for card in cards:
                raw = extract_job(card)
                if raw and raw.get("title"):
                    detail_url = raw.get("url")
                    if detail_url:
                        raw["description"] = fetch_job_description(client, detail_url)
                    all_jobs.append(normalize_job(raw))
                    page_jobs += 1
            print(f"  Page {page}: {page_jobs} jobs (1 detail fetch per job)")
    finally:
        client.close()

    if fmt == "json":
        export_json(all_jobs, output)
    else:
        export_csv(all_jobs, output)

    print(f"\nWrote {len(all_jobs)} jobs to {output}")
    return len(all_jobs)


@app.command("hellowork")
def hellowork(
    query: Annotated[
        str, typer.Option("--query", "-q", help="Job title / keyword search")
    ] = DEFAULT_QUERY,
    location: Annotated[
        str, typer.Option("--location", "-l", help="Location (city, department or 'france')")
    ] = DEFAULT_LOCATION,
    contracts: Annotated[
        list[str],
        typer.Option(
            "--contracts", "-c",
            help="Contract types: contractor, fixed-term, permanent, temporary, internship. Repeatable.",
        ),
    ] = DEFAULT_CONTRACTS,
    remote: Annotated[
        list[str],
        typer.Option(
            "--remote", "-r",
            help="Remote types (accepted for CLI consistency; HelloWork has no remote filter)",
        ),
    ] = [],
    experience: Annotated[
        list[str],
        typer.Option(
            "--experience", "-e",
            help="Experience levels (accepted for CLI consistency; HelloWork has no experience filter)",
        ),
    ] = [],
    sort: Annotated[
        str, typer.Option("--sort", "-s", help="Sort order (accepted; HelloWork sorts by relevance)")
    ] = DEFAULT_SORT,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output file path")
    ] = Path("hellowork_jobs.csv"),
    max_pages: Annotated[
        Optional[int],
        typer.Option("--max-pages", "-p", help="Limit to N pages"),
    ] = None,
    fmt: Annotated[
        Literal["csv", "json"],
        typer.Option("--format", "-f", help="Output format"),
    ] = "csv",
) -> None:
    """Scrape job listings from hellowork.com.


    Defaults to data engineer jobs across France for permanent, fixed-term
    and contractor roles. Job descriptions are in French (the pipeline
    translates fr -> en downstream).
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if remote:
        logger.info("HelloWork has no remote filter -- ignoring --remote %s", remote)
    if experience:
        logger.info("HelloWork has no experience filter -- ignoring --experience %s", experience)
    if sort != DEFAULT_SORT:
        logger.info("HelloWork has no server-side sort -- ignoring --sort %s", sort)

    if fmt == "json" and output == Path("hellowork_jobs.csv"):
        output = Path("hellowork_jobs.json")

    list_url = build_url(query, location, contracts)
    print(f"Search URL: {list_url}")

    scrape(list_url, output, max_pages, fmt)


if __name__ == "__main__":
    app()
