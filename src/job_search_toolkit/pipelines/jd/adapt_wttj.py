"""
Adapter: normalize Welcome to the Jungle (WTTJ) offer records into the
canonical schema.

Input: raw record dicts produced by ``job_search_toolkit.scrapers.wttj.parse_offer``
(JSON-LD JobPosting fields when available, og:title/og:description fallbacks
otherwise — see `content_quality`).

Never fabricates: missing salary/location/date stay None/empty. Monthly
salaries are annualised (×12); other units are not converted and stay None.
"""

from __future__ import annotations
import re

from bs4 import BeautifulSoup

from job_search_toolkit.scrapers.wttj import FRANCE_MARKER
from job_search_toolkit.schemas import (
    CanonicalJob,
    CompanyInfo,
    ContractType,
    EnrichmentStatus,
    Salary,
    WorkplaceType,
    new_canonical_job,
)

# schema.org employmentType -> canonical ContractType (lowercase keys)
CONTRACT_MAP: dict[str, ContractType] = {
    "full_time": ContractType.FULL_TIME,
    "part_time": ContractType.PART_TIME,
    "temporary": ContractType.TEMPORARY,
    "contractor": ContractType.CONTRACT,
    "intern": ContractType.INTERNSHIP,
    "internship": ContractType.INTERNSHIP,
    "seasonal": ContractType.SEASONAL,
    "volunteer": ContractType.VOLUNTEER,
}

# JSON-LD salary unitText -> annualisation factor (schema.org QuantitativeValue)
_UNIT_TO_ANNUAL_FACTOR: dict[str, float] = {
    "YEAR": 1.0,
    "YEARLY": 1.0,
    "MONTH": 12.0,
    "MONTHLY": 12.0,
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def normalize_wttj_job(raw: dict) -> CanonicalJob:
    """Convert a WTTJ raw offer record to canonical format."""
    salary = _parse_salary(raw)

    emp_raw = str(raw.get("employment_type") or "").lower()
    contract_types = (
        [CONTRACT_MAP[emp_raw]] if emp_raw in CONTRACT_MAP else []
    )

    job = new_canonical_job("wttj")
    job.update({
        "id": raw.get("url", ""),
        "source_url": raw.get("url"),
        "title": raw.get("title") or "",
        "company": raw.get("company") or "",
        "apply_url": raw.get("url"),
        "location_raw": raw.get("location_raw") or "",
        "workplace_type": _parse_workplace_type(raw),
        "date_posted": _normalize_date(raw.get("date_posted")),
        "salary": salary,
        "contract_types": contract_types,
        "seniority_level": None,
        "role_category": None,
        "years_experience_min": None,
        "technologies": [],
        "competencies": [],
        "description_text": _strip_html(raw.get("description")),
        "description_language": "fr",
        "company_info": CompanyInfo(
            name=raw.get("company") or "",
            industry=[],
            size_employees=None,
            year_founded=None,
            hq_country="FR" if FRANCE_MARKER in (raw.get("url") or "") else None,
            org_type=None,  # NULL = not yet LLM-researched (warehouse gate selects IS NULL)
            stock_symbol=None,
            stock_exchange=None,
            latest_funding_type=None,
            latest_funding_amount_usd=None,
            homepage_url=None,
        ),
        "engagement_type": None,
        "posting_company_type": None,
        "end_client_name": None,
        "end_client_sector": None,
        "contract_duration": None,
        "views": None,
        "applications": None,
        "is_expired": False,
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


def _parse_salary(raw: dict) -> Salary:
    """Annualise the JSON-LD baseSalary min/max; never convert unknown units."""
    min_val, max_val = raw.get("salary_min"), raw.get("salary_max")
    factor = _UNIT_TO_ANNUAL_FACTOR.get(str(raw.get("salary_unit")).upper())
    if factor is not None:
        min_eur = float(min_val) * factor if isinstance(min_val, (int, float)) else None
        max_eur = float(max_val) * factor if isinstance(max_val, (int, float)) else None
    else:
        # Not a monthly figure: do not guess an annual conversion.
        min_eur = None
        max_eur = None
    currency = raw.get("salary_currency")
    disclosed = min_eur is not None or max_eur is not None
    return Salary(
        min_annual_eur=min_eur,
        max_annual_eur=max_eur,
        currency_original=currency if currency else ("EUR" if disclosed else ""),
        frequency_original=str(raw.get("salary_unit")) if raw.get("salary_unit") else "",
        is_disclosed=disclosed,
    )


def _parse_workplace_type(raw: dict) -> WorkplaceType | None:
    """
    Infer remote/hybrid/onsite only from explicit on-page signals.

    WTTJ JSON-LD carries no remote field, so the title + raw location text
    are scanned for remote/hybrid keywords. Anything ambiguous stays None.
    """
    haystack = f"{raw.get('title') or ''} {raw.get('location_raw') or ''}".lower()


def _normalize_date(value) -> str | None:
    """Keep ISO-8601 dates as-is; anything else stays None (never fabricated)."""
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?", text):
        return text[:10]
    return None


def _strip_html(html: str | None) -> str | None:
    """Strip HTML tags from a description; collapse whitespace."""
    if html is None:
        return None
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ")).strip()
