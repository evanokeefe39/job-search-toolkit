"""
Scrape job listings from hiringcafe.com via its Next.js SSR data route.

No auth, no API key, no headless browser. Structured JSON with ~90 fields per job.
Same search params as scrape_freework.py for drop-in comparison.

Usage:
    uv run python scrape_hiringcafe.py
    uv run python scrape_hiringcafe.py --query "python developer" --contracts contractor
    uv run python scrape_hiringcafe.py --query "devops" --remote full --experience senior
"""

import csv
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Annotated, Optional
from urllib.parse import quote_plus

import httpx
import typer

app = typer.Typer(no_args_is_help=False)

# --- Defaults (match free-work scraper) ---
DEFAULT_LOCATION = "Paris, France"
DEFAULT_QUERY = "data engineer"
DEFAULT_CONTRACTS = ["contractor", "fixed-term", "permanent"]
DEFAULT_REMOTE = ["partial", "full", "none"]
DEFAULT_EXPERIENCE = ["senior", "intermediate", "junior"]
DEFAULT_SORT = "date"

# free-work → HiringCafe contract mapping
CONTRACT_MAP = {
    "contractor": "Contract",
    "fixed-term": "Contract",
    "permanent": "Full Time",
    "internship": "Internship",
    "temporary": "Temporary",
    "seasonal": "Seasonal",
    "volunteer": "Volunteer",
}

# free-work → HiringCafe remote mapping
REMOTE_MAP = {
    "partial": "Hybrid",
    "full": "Remote",
    "none": "Onsite",
}

# free-work → HiringCafe experience mapping
EXPERIENCE_MAP = {
    "junior": "Entry Level",
    "intermediate": "Mid Level",
    "senior": "Senior Level",
}

BASE_URL = "https://hiringcafe.com"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-GB,en;q=0.9",
}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geocoding (Nominatim, free, no API key)
# ---------------------------------------------------------------------------

def geocode_location(name: str) -> dict:
    """Resolve a plain location name into HiringCafe's location object."""
    try:
        resp = httpx.get(
            NOMINATIM_URL,
            params={
                "q": name,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 1,
                "accept-language": "en",
            },
            headers={"User-Agent": "HiringCafeScraper/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Geocoding failed for '{name}': {exc}") from exc

    if not results:
        raise RuntimeError(f"Could not geocode '{name}'. Try a simpler name.")

    r = results[0]
    address = r.get("address", {})
    addr_type = r.get("addresstype", "")

    country = address.get("country", "")
    country_code = (address.get("country_code") or "").upper()
    state = address.get("state", "")
    state_code = (address.get("ISO3166-2-lvl4") or "").split("-")[-1] or state
    city = address.get("city") or address.get("town") or address.get("village") or ""

    components: list[dict] = []
    formatted_parts: list[str] = []

    if addr_type == "country":
        types = ["country"]
    elif addr_type in ("state", "province", "region"):
        types = ["administrative_area_level_1"]
        components.append({
            "long_name": state or name, "short_name": state_code or name,
            "types": ["administrative_area_level_1"],
        })
        formatted_parts.append(state or name)
    else:
        types = ["locality"]
        locality = city or name
        components.append({
            "long_name": locality, "short_name": locality, "types": ["locality"],
        })
        formatted_parts.append(locality)
        if state:
            components.append({
                "long_name": state, "short_name": state_code,
                "types": ["administrative_area_level_1"],
            })
            formatted_parts.append(state_code or state)

    if country:
        components.append({
            "long_name": country, "short_name": country_code, "types": ["country"],
        })
        formatted_parts.append(country)

    location = {
        "formatted_address": ", ".join(formatted_parts) if formatted_parts else name,
        "types": types,
        "id": "user_defined",
        "address_components": components,
        "options": {},
    }

    lat, lon = r.get("lat"), r.get("lon")
    if lat and lon:
        location["geometry"] = {"location": {"lat": float(lat), "lon": float(lon)}}

    logger.info("Geocoded '%s' → %s (%s)", name, location["formatted_address"], types[0])
    return location


# ---------------------------------------------------------------------------
# HiringCafe client
# ---------------------------------------------------------------------------

class HiringCafeClient:
    """Rate-limited HTTP client for the Next.js data route."""

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay
        self._build_id: str | None = None
        self._last_request_at = 0.0
        self.client = httpx.Client(headers=HEADERS, timeout=30)

    @property
    def build_id(self) -> str:
        if self._build_id is None:
            self._build_id = self._fetch_build_id()
        return self._build_id

    def _fetch_build_id(self) -> str:
        resp = self._request("GET", BASE_URL + "/")
        match = NEXT_DATA_RE.search(resp.text)
        if not match:
            raise RuntimeError(
                "Could not find __NEXT_DATA__ on homepage. "
                "Site structure may have changed or a bot challenge was served."
            )
        payload = json.loads(match.group(1))
        bid = payload.get("buildId")
        if not bid:
            raise RuntimeError("No buildId in __NEXT_DATA__.")
        logger.info("buildId: %s", bid)
        return bid

    def search_page(self, search_state: dict, page: int) -> dict:
        """Fetch one page of results. Returns pageProps dict."""
        encoded = quote_plus(json.dumps(search_state, separators=(",", ":")))
        url = f"{BASE_URL}/_next/data/{self.build_id}/index.json?searchState={encoded}&page={page}"
        resp = self._request("GET", url, extra_headers={"x-nextjs-data": "1", "Accept": "*/*"})
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Non-JSON response for page {page}: {resp.text[:200]}") from exc

        pp = payload.get("pageProps")
        if pp is None:
            raise RuntimeError(f"No pageProps in response for page {page}.")
        return pp

    def _request(self, method: str, url: str, extra_headers: dict | None = None) -> httpx.Response:
        self._throttle()
        headers = {**HEADERS, **(extra_headers or {})}
        resp = self.client.request(method, url, headers=headers)
        resp.raise_for_status()
        return resp

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self.delay + random.uniform(0.0, self.delay * 0.5) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def close(self) -> None:
        self.client.close()


# ---------------------------------------------------------------------------
# Search & pagination
# ---------------------------------------------------------------------------

def build_search_state(
    query: str = "",
    location: str | None = None,
    remote_types: list[str] | None = None,
    contract_types: list[str] | None = None,
    experience_levels: list[str] | None = None,
    days_old: int | None = None,
    sort_by: str = "date",
) -> dict:
    """Build the searchState dict for HiringCafe's data route."""
    state: dict = {}

    if query:
        state["searchQuery"] = query
    if sort_by:
        state["sortBy"] = sort_by
    if location:
        state["locations"] = [geocode_location(location)]
    if remote_types:
        state["workplaceTypes"] = remote_types
    if contract_types:
        state["commitmentTypes"] = contract_types
    if experience_levels:
        state["seniorityLevel"] = experience_levels
    if days_old is not None:
        state["dateFetchedPastNDays"] = days_old

    return state


def fetch_all_jobs(
    client: HiringCafeClient,
    search_state: dict,
    max_pages: int = 50,
) -> list[dict]:
    """Paginate through all results, deduplicating on objectID."""
    jobs: list[dict] = []
    seen: set[str] = set()

    for page in range(max_pages):
        pp = client.search_page(search_state, page)

        hits = pp.get("ssrHits") or []
        total = pp.get("ssrTotalCount")
        is_last = pp.get("ssrIsLastPage", False)

        if page == 0 and total:
            logger.info("Total matching jobs: %s", f"{total:,}")

        for hit in hits:
            oid = hit.get("objectID") or hit.get("id")
            if oid and oid in seen:
                continue
            if oid:
                seen.add(oid)
            jobs.append(hit)

        logger.info("Page %d: +%d hits (total collected: %d)", page, len(hits), len(jobs))

        if is_last or not hits:
            logger.info("Last page reached.")
            break
    else:
        logger.info("Max pages (%d) reached.", max_pages)

    return jobs


from schemas import (
    CanonicalJob,
    CompanyInfo,
    CompanyType,
    ContractType,
    EngagementType,
    EnrichmentStatus,
    RoleCategory,
    Salary,
    SeniorityLevel,
    WorkplaceType,
    new_canonical_job,
)

# Fixed EUR/USD rate for salary normalization (updated periodically)
EUR_USD_RATE = 0.92

# HiringCafe job_category → RoleCategory heuristic map
ROLE_CATEGORY_MAP: dict[str, RoleCategory] = {
    "Data and Analytics": RoleCategory.DATA_ENGINEER,
    "Software Development": RoleCategory.SOFTWARE_ENGINEER,
    "Engineering": RoleCategory.PLATFORM_ENGINEER,
    "Information Technology": RoleCategory.OTHER,
    "Project and Program Management": RoleCategory.PRODUCT_MANAGER,
}

# HiringCafe workplace_type → WorkplaceType
WORKPLACE_MAP: dict[str, WorkplaceType] = {
    "Remote": WorkplaceType.REMOTE,
    "Hybrid": WorkplaceType.HYBRID,
    "Onsite": WorkplaceType.ONSITE,
}

# HiringCafe commitment → ContractType
COMMITMENT_NORM_MAP: dict[str, ContractType] = {
    "Full Time": ContractType.FULL_TIME,
    "Part Time": ContractType.PART_TIME,
    "Contract": ContractType.CONTRACT,
    "Internship": ContractType.INTERNSHIP,
    "Temporary": ContractType.TEMPORARY,
    "Seasonal": ContractType.SEASONAL,
    "Volunteer": ContractType.VOLUNTEER,
}

# HiringCafe seniority → SeniorityLevel
SENIORITY_NORM_MAP: dict[str, SeniorityLevel] = {
    "No Prior Experience Required": SeniorityLevel.ENTRY,
    "Entry Level": SeniorityLevel.ENTRY,
    "Mid Level": SeniorityLevel.MID,
    "Senior Level": SeniorityLevel.SENIOR,
}

# HiringCafe organization_type → CompanyType
ORG_TYPE_MAP: dict[str, CompanyType] = {
    "Public": CompanyType.PUBLIC,
    "Private": CompanyType.PRIVATE,
}


def normalize_job(hit: dict) -> CanonicalJob:
    """Convert a raw HiringCafe hit into a CanonicalJob."""
    p = hit.get("v5_processed_job_data") or {}
    j = hit.get("job_information") or {}
    c = hit.get("enriched_company_data") or {}

    # --- Salary normalization ---
    currency = (p.get("listed_compensation_currency") or "USD").upper()
    freq = (p.get("listed_compensation_frequency") or "yearly").lower()
    min_raw = p.get("yearly_min_compensation")
    max_raw = p.get("yearly_max_compensation")

    if min_raw is not None or max_raw is not None:
        rate = EUR_USD_RATE if currency == "USD" else 1.0
        salary = Salary(
            min_annual_eur=round(min_raw * rate) if min_raw is not None else None,
            max_annual_eur=round(max_raw * rate) if max_raw is not None else None,
            currency_original=currency,
            frequency_original=freq,
            is_disclosed=True,
        )
    else:
        salary = Salary(
            min_annual_eur=None,
            max_annual_eur=None,
            currency_original=currency,
            frequency_original=freq,
            is_disclosed=False,
        )

    # --- Company info ---
    org_type_raw = (c.get("organization_type") or "").title()
    company_type = ORG_TYPE_MAP.get(org_type_raw, CompanyType.UNKNOWN)
    industries_raw = c.get("industries") or []

    company_info = CompanyInfo(
        name=c.get("name") or p.get("company_name") or "",
        industry=industries_raw,
        size_employees=c.get("nb_employees"),
        year_founded=c.get("year_founded"),
        hq_country=c.get("hq_country"),
        org_type=company_type,
        stock_symbol=c.get("stock_symbol"),
        stock_exchange=c.get("stock_exchange"),
        latest_funding_type=c.get("latest_funding_type"),
        latest_funding_amount_usd=c.get("latest_funding_amount"),
        homepage_url=c.get("homepage_uri"),
    )

    # --- Workplace type ---
    wp_raw = p.get("workplace_type") or ""
    workplace_type = WORKPLACE_MAP.get(wp_raw)

    # --- Contract types ---
    commitments = p.get("commitment") or []
    contract_types = list(dict.fromkeys(
        COMMITMENT_NORM_MAP.get(c, ContractType.FULL_TIME) for c in commitments
    ))

    # --- Seniority ---
    sen_raw = p.get("seniority_level") or ""
    seniority_level = SENIORITY_NORM_MAP.get(sen_raw)

    # --- Role category ---
    job_cat = p.get("job_category") or ""
    role_category = ROLE_CATEGORY_MAP.get(job_cat, RoleCategory.OTHER)

    # --- Date ---
    date_str = p.get("estimated_publish_date", "")
    if date_str and "T" in str(date_str):
        date_str = str(date_str)[:10]

    job = new_canonical_job("hiringcafe")
    job.update({
        "id": hit.get("objectID") or hit.get("id", ""),
        "source_url": None,
        "title": j.get("title", ""),
        "company": p.get("company_name") or c.get("name", ""),
        "apply_url": hit.get("apply_url"),
        "location_raw": p.get("formatted_workplace_location") or "",
        "workplace_type": workplace_type,
        "date_posted": date_str or None,
        "salary": salary,
        "contract_types": contract_types,
        "seniority_level": seniority_level,
        "role_category": role_category,
        "years_experience_min": p.get("min_industry_and_role_yoe"),
        "technologies": p.get("technical_tools") or [],
        "competencies": p.get("role_activities") or [],
        "description_text": p.get("requirements_summary") or "",
        "description_language": "en",
        "company_info": company_info,
        "engagement_type": EngagementType.DIRECT,
        "posting_company_type": "end_client",
        "end_client_name": None,
        "end_client_sector": None,
        "contract_duration": None,
        "views": j.get("num_views"),
        "applications": j.get("num_applies"),
        "is_expired": hit.get("is_expired", False),
        "_enrichment": EnrichmentStatus(
            tech_extracted=bool(p.get("technical_tools")),
            company_researched=bool(c.get("name")),
            vertical_classified=True,
            translated=True,
            scored=False,
        ),
        "_source": hit,
    })
    return job


def flatten_canonical(job: CanonicalJob) -> dict:
    """Flatten a CanonicalJob into a CSV-friendly flat dict."""
    s = job.get("salary") or {}
    c = job.get("company_info") or {}
    sc = job.get("scores") or {}
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
        "company_stock_symbol": c.get("stock_symbol"),
        "company_funding_type": c.get("latest_funding_type"),
        "company_funding_amount_usd": c.get("latest_funding_amount_usd"),
        "engagement_type": job.get("engagement_type"),
        "posting_company_type": job.get("posting_company_type"),
        "end_client_name": job.get("end_client_name"),
        "end_client_sector": job.get("end_client_sector"),
        "views": job.get("views"),
        "applications": job.get("applications"),
        "is_expired": job.get("is_expired"),
        "overall_score": job.get("overall_score"),
        "recommendation_tier": job.get("recommendation_tier"),
        "scores_pay": sc.get("pay"),
        "scores_flexibility": sc.get("flexibility"),
        "scores_low_responsibility": sc.get("low_responsibility"),
        "scores_tech_match": sc.get("tech_match"),
        "scores_company_quality": sc.get("company_quality"),
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@app.command()
def scrape(
    query: Annotated[
        str, typer.Option("--query", "-q", help="Search query")
    ] = DEFAULT_QUERY,
    location: Annotated[
        str, typer.Option("--location", "-l", help="Location name (geocoded via Nominatim)")
    ] = DEFAULT_LOCATION,
    contracts: Annotated[
        list[str],
        typer.Option(
            "--contracts", "-c",
            help="Contract types: contractor, fixed-term, permanent, internship, temporary",
        ),
    ] = DEFAULT_CONTRACTS,
    remote: Annotated[
        list[str],
        typer.Option(
            "--remote", "-r",
            help="Remote types: full, partial, none",
        ),
    ] = DEFAULT_REMOTE,
    experience: Annotated[
        list[str],
        typer.Option(
            "--experience", "-e",
            help="Experience levels: junior, intermediate, senior",
        ),
    ] = DEFAULT_EXPERIENCE,
    days_old: Annotated[
        Optional[int], typer.Option("--days-old", "-d", help="Only jobs from last N days")
    ] = None,
    sort_by: Annotated[
        str, typer.Option("--sort", "-s", help="Sort: date or relevance")
    ] = DEFAULT_SORT,
    max_pages: Annotated[
        int, typer.Option("--max-pages", "-p", help="Max pages to fetch")
    ] = 50,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output file base (no extension)")
    ] = Path("hiringcafe_jobs"),
    delay: Annotated[
        float, typer.Option("--delay", help="Seconds between requests (with jitter)")
    ] = 1.0,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Verbose logging")
    ] = False,
) -> None:
    """Scrape job listings from hiringcafe.com.

    Defaults match free-work scraper: data engineer jobs in Paris,
    all contract types, all experience levels, all remote types.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Map free-work enums to HiringCafe enums
    hc_contracts = list(dict.fromkeys(CONTRACT_MAP[c] for c in contracts if c in CONTRACT_MAP))
    hc_remote = list(dict.fromkeys(REMOTE_MAP[r] for r in remote if r in REMOTE_MAP))
    hc_experience = list(dict.fromkeys(EXPERIENCE_MAP[e] for e in experience if e in EXPERIENCE_MAP))

    logger.info("Query: %s | Location: %s", query, location)
    logger.info("Contracts: %s → %s", contracts, hc_contracts)
    logger.info("Remote: %s → %s", remote, hc_remote)
    logger.info("Experience: %s → %s", experience, hc_experience)

    search_state = build_search_state(
        query=query,
        location=location,
        remote_types=hc_remote,
        contract_types=hc_contracts,
        experience_levels=hc_experience,
        days_old=days_old,
        sort_by=sort_by,
    )

    client = HiringCafeClient(delay=delay)
    try:
        raw_hits = fetch_all_jobs(client, search_state, max_pages=max_pages)
    finally:
        client.close()

    logger.info("Total collected: %d raw hits", len(raw_hits))

    # Normalize to canonical schema
    jobs = [normalize_job(h) for h in raw_hits]

    export_json(jobs, Path(str(output) + ".json"))
    export_csv(jobs, Path(str(output) + ".csv"))

    # Show summary
    if jobs:
        titles = [j.get("title", "") for j in jobs]
        companies = {j.get("company", "") for j in jobs}
        logger.info("Unique companies: %d", len(companies - {""}))
        logger.info("Sample titles: %s", titles[:5])


if __name__ == "__main__":
    app()
