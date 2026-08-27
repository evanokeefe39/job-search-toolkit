"""
Adapter: normalize builtin.com (France) raw job records into the canonical schema.

The builtin scraper outputs records with these fields:
  job_id, slug, title, company, location_raw, workplace_raw, salary_raw,
  posted_text, detail_url, description, date_posted, employment_type,
  salary_min_annual, salary_max_annual
"""

from __future__ import annotations

from job_search_toolkit.schemas import (
    CanonicalJob,
    CompanyInfo,
    ContractType,
    EnrichmentStatus,
    Salary,
    WorkplaceType,
    new_canonical_job,
)

# Built In workplace tag -> canonical WorkplaceType
WORKPLACE_MAP: dict[str, WorkplaceType] = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "in-office": WorkplaceType.ONSITE,
}

EMPLOYMENT_TYPE_MAP: dict[str, ContractType] = {
    "FULL_TIME": ContractType.FULL_TIME,
    "PART_TIME": ContractType.PART_TIME,
    "CONTRACTOR": ContractType.CONTRACT,
    "TEMPORARY": ContractType.TEMPORARY,
    "INTERN": ContractType.INTERNSHIP,
}


def normalize_builtin_job(raw: dict) -> CanonicalJob:
    """Convert a Built In France job record to canonical format."""
    job = new_canonical_job("builtin")

    job["id"] = f"builtin-{raw['job_id']}"
    job["title"] = raw.get("title") or ""
    job["company"] = raw.get("company") or ""
    job["source_url"] = raw.get("detail_url")
    job["apply_url"] = raw.get("apply_url") or raw.get("detail_url")
    job["location_raw"] = raw.get("location_raw") or ""
    job["date_posted"] = raw.get("date_posted") or None
    job["description_text"] = raw.get("description") or ""
    job["description_language"] = "en"

    salary_min = raw.get("salary_min_annual")
    salary_max = raw.get("salary_max_annual")
    currency = raw.get("salary_currency")
    disclosed = salary_min is not None or salary_max is not None
    job["salary"] = Salary(
        min_annual_eur=salary_min if disclosed else None,
        max_annual_eur=salary_max if disclosed else None,
        # Source emits no currency; carry it through only when actually present.
        currency_original=str(currency) if currency else "",
        frequency_original="yearly" if disclosed else "",
        is_disclosed=disclosed,
    )

    contract = EMPLOYMENT_TYPE_MAP.get(raw.get("employment_type") or "")
    if contract:
        job["contract_types"] = [contract]

    workplace = _parse_workplace_type(raw.get("workplace_raw"))
    if workplace:
        job["workplace_type"] = workplace

    job.update({
        "engagement_type": None,
        "posting_company_type": None,
        "company_info": CompanyInfo(
            name=raw.get("company") or "",
            industry=[],
            size_employees=None,
            year_founded=None,
            hq_country=None,
            org_type=None,  # NULL = not yet LLM-researched (warehouse gate selects IS NULL)
            stock_symbol=None,
            stock_exchange=None,
            latest_funding_type=None,
            latest_funding_amount_usd=None,
            homepage_url=None,
        ),
        "_enrichment": EnrichmentStatus(
            tech_extracted=False,
            company_researched=False,
            vertical_classified=False,
            translated=False,
            scored=False,
        ),
        "scores": None,
        "overall_score": None,
        "_source": raw,
    })
    return job


def _parse_workplace_type(text: str | None) -> WorkplaceType | None:
    """Map a Built In workplace tag ('Remote', 'Hybrid', 'In-Office or Remote') to
    a canonical value; combined tags resolve to hybrid."""
    if not text:
        return None
    lowered = text.lower()
    if "remote" in lowered and ("hybrid" in lowered or "in-office" in lowered):
        return WorkplaceType.HYBRID
    for key in ("remote", "hybrid", "in-office"):
        if key in lowered:
            return WORKPLACE_MAP[key]
    return None
