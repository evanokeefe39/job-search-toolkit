"""
Adapter: normalize free-work.com job records into the canonical schema.

The free-work scraper outputs records with these fields:
  title, url, company, skills, date_posted, description,
  contract_types, start_date, duration, pay, rate, remote_type, location

After enrichment stages, additional fields are added:
  description_en, extracted_technologies, competencies,
  seniority_level, role_category, posting_company_type,
  end_client_name, end_client_sector, engagement_type,
  company_stats: { company_type, employee_count, public_private, stock_perf, info_quality }
"""

from __future__ import annotations

import re

from job_search_toolkit.schemas import (
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

REMOTE_MAP: dict[str, WorkplaceType] = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "onsite": WorkplaceType.ONSITE,
    "": WorkplaceType.ONSITE,
}

# free-work contract type -> ContractType
CONTRACT_MAP: dict[str, ContractType] = {
    "contractor": ContractType.CONTRACT, "Contractor": ContractType.CONTRACT,
    "fixed-term": ContractType.CONTRACT, "Fixed Term": ContractType.CONTRACT,
    "permanent": ContractType.FULL_TIME, "Permanent": ContractType.FULL_TIME,
    "internship": ContractType.INTERNSHIP, "Internship": ContractType.INTERNSHIP,
    "temporary": ContractType.TEMPORARY, "Temporary": ContractType.TEMPORARY,
}

# free-work seniority -> SeniorityLevel
SENIORITY_MAP: dict[str, SeniorityLevel] = {
    "junior": SeniorityLevel.JUNIOR,
    "intermediate": SeniorityLevel.MID,
    "senior": SeniorityLevel.SENIOR,
    "lead": SeniorityLevel.LEAD,
    "architect": SeniorityLevel.LEAD,
    "manager": SeniorityLevel.MANAGER,
}

# free-work role_category → RoleCategory
ROLE_MAP: dict[str, RoleCategory] = {
    "data_engineer": RoleCategory.DATA_ENGINEER,
    "data_analyst": RoleCategory.DATA_ANALYST,
    "data_scientist": RoleCategory.DATA_SCIENTIST,
    "ml_engineer": RoleCategory.ML_ENGINEER,
    "analytics_engineer": RoleCategory.ANALYTICS_ENGINEER,
    "data_platform_engineer": RoleCategory.PLATFORM_ENGINEER,
    "devops_data": RoleCategory.DEVOPS_ENGINEER,
    "data_product_manager": RoleCategory.PRODUCT_MANAGER,
}

# free-work company_type → CompanyType
COMPANY_TYPE_MAP: dict[str, CompanyType] = {
    "startup": CompanyType.STARTUP,
    "enterprise": CompanyType.ENTERPRISE,
    "consulting_firm": CompanyType.CONSULTING_FIRM,
    "public_company": CompanyType.PUBLIC,
    "private_company": CompanyType.PRIVATE,
}


def normalize_freework_job(raw: dict) -> CanonicalJob:
    """Convert a free-work job record to canonical format."""

    # --- Salary: parse pay/rate strings ---
    pay_str = raw.get("pay") or ""
    rate_str = raw.get("rate") or ""
    min_eur, max_eur = _parse_compensation(pay_str, rate_str)

    salary = Salary(
        min_annual_eur=min_eur if min_eur > 0 else None,
        max_annual_eur=max_eur if max_eur > 0 else None,
        currency_original="EUR",
        frequency_original="daily" if rate_str and not pay_str else "yearly",
        is_disclosed=min_eur > 0 or max_eur > 0,
    )

    # --- Workplace ---
    remote_raw = (raw.get("remote_type") or "").lower()
    workplace_type = REMOTE_MAP.get(remote_raw)

    # --- Contract types ---
    contracts_raw = raw.get("contract_types") or []
    contract_types = list(dict.fromkeys(
        CONTRACT_MAP.get(c, ContractType.FULL_TIME)
        for c in contracts_raw if isinstance(c, str)
    ))

    # --- Seniority (from enrichment if present, else from raw title) ---
    sen_raw = (raw.get("seniority_level") or "").lower()
    seniority_level = SENIORITY_MAP.get(sen_raw)

    # --- Role category (from enrichment if present) ---
    role_raw = (raw.get("role_category") or "").lower()
    role_category = ROLE_MAP.get(role_raw, RoleCategory.OTHER)

    # --- Company info (from enrichment if present) ---
    stats = raw.get("company_stats") or {}
    company_info = CompanyInfo(
        name=raw.get("company", ""),
        industry=[],
        size_employees=stats.get("employee_count"),
        year_founded=None,
        hq_country=None,
        # NULL = not yet LLM-researched; 'unknown' is reserved for a real
        # LLM result. The warehouse company gate selects on IS NULL.
        org_type=(
            COMPANY_TYPE_MAP.get(stats["company_type"])
            if stats.get("company_type") else None
        ),
        stock_symbol=stats.get("stock_symbol"),
        stock_exchange=None,
        latest_funding_type=None,
        latest_funding_amount_usd=None,
        homepage_url=None,
    )

    # --- Date ---
    date_posted = raw.get("date_posted")
    if date_posted:
        # DD/MM/YYYY → ISO
        date_posted = _normalize_date(date_posted)

    # --- Enrichment status ---
    enrichment = EnrichmentStatus(
        tech_extracted=bool(raw.get("extracted_technologies")),
        company_researched=bool(raw.get("company_stats")),
        vertical_classified=bool(raw.get("engagement_type")),
        translated=bool(raw.get("description_en")),
        scored=bool(raw.get("scores")),
    )

    # --- Engagement ---
    engagement_raw = (raw.get("engagement_type") or "").lower()
    engagement_map: dict[str, EngagementType] = {
        "direct": EngagementType.DIRECT,
        "consulting": EngagementType.CONSULTING,
    }
    # NULL = not yet LLM-classified (see org_type above).
    engagement_value = (
        engagement_map.get(engagement_raw)
        if raw.get("engagement_type") else None
    )

    # --- Scores ---
    scores = raw.get("scores")
    if scores:
        from job_search_toolkit.schemas import Scores
        scores = Scores(
            pay=scores.get("pay", 0),
            flexibility=scores.get("flexibility", 0),
            low_responsibility=scores.get("low_responsibility", 0),
            tech_match=scores.get("tech_match", 0),
        )

    job = new_canonical_job("freework")
    job.update({
        "id": raw.get("url", ""),  # URL as unique ID for free-work
        "source_url": raw.get("url"),
        "title": raw.get("title", ""),
        "company": raw.get("company", ""),
        "apply_url": raw.get("url"),
        "location_raw": raw.get("location", ""),
        "workplace_type": workplace_type,
        "date_posted": date_posted,
        "salary": salary,
        "contract_types": contract_types,
        "seniority_level": seniority_level,
        "role_category": role_category,
        "years_experience_min": None,
        "technologies": raw.get("extracted_technologies") or raw.get("skills") or [],
        "competencies": raw.get("competencies") or [],
        "description_text": raw.get("description_en") or raw.get("description", ""),
        "description_language": "en" if raw.get("description_en") else "fr",
        "company_info": company_info,
        "engagement_type": engagement_value,
        "posting_company_type": raw.get("posting_company_type"),
        "end_client_name": raw.get("end_client_name"),
        "end_client_sector": raw.get("end_client_sector"),
        "contract_duration": raw.get("duration"),
        "views": None,
        "applications": None,
        "is_expired": False,
        "_enrichment": enrichment,
        "scores": scores,
        "overall_score": raw.get("overall_score"),
        "_source": raw,
    })
    return job


def _parse_compensation(pay_str: str, rate_str: str) -> tuple[float, float]:
    """Parse free-work pay/rate strings into annual EUR (min, max).

    Handles: "40k-75k €", "450-650 €" (daily rate), "1 260 €".
    """
    def _parse_one(v: str) -> float:
        v = v.strip().replace("\xa0", "").replace("\u202f", "").replace(" ", "")
        if v.lower().endswith("k"):
            return float(v[:-1]) * 1000
        return float(v)

    if pay_str:
        parts = re.split(r"\s*[-–—]\s*", pay_str.replace("\xa0", " "))
        parts = [re.sub(r"\s*[€¤$].*", "", p).strip() for p in parts]
        nums = [_parse_one(p) for p in parts if p]
        if len(nums) >= 2:
            return nums[0], nums[-1]
        if len(nums) == 1:
            return nums[0], nums[0]

    if rate_str:
        parts = re.split(r"\s*[-–—]\s*", rate_str.replace("\xa0", " "))
        parts = [re.sub(r"\s*[€¤$].*", "", p).strip() for p in parts]
        nums = [_parse_one(p) for p in parts if p]
        if len(nums) >= 2:
            return nums[0] * 220, nums[-1] * 220
        if len(nums) == 1:
            return nums[0] * 220, nums[0] * 220
    return 0.0, 0.0

def _normalize_date(d: str) -> str | None:
    """MM/DD/YYYY -> YYYY-MM-DD (free-work uses US date format)."""
    match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d or "")
    if not match:
        if re.match(r"\d{4}-\d{2}-\d{2}", d or ""):
            return d
        return d or None
    month, day, year = int(match.group(1)), int(match.group(2)), match.group(3)
    return f"{year}-{month:02d}-{day:02d}"
