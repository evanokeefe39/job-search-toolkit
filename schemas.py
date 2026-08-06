"""
Canonical job schema for multi-board job aggregation.

Every scraper normalizes its output to this schema. The scoring pipeline
reads only these fields — no board-specific logic outside the adapter layer.

Design principles:
- One field per concept — no synonyms (no "pay" vs "rate" vs "salary")
- Arrays for multi-valued fields, never delimited strings
- Snake_case normalized enums, not board-specific labels
- Numeric types for computable fields (salary, counts, dates)
- All fields optional except id/title/source_board
- Raw source preserved in _source for debugging/audit
"""

from __future__ import annotations

from enum import StrEnum
from typing import NotRequired, TypedDict


# ---------------------------------------------------------------------------
# Normalized enums
# ---------------------------------------------------------------------------

class WorkplaceType(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class ContractType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    SEASONAL = "seasonal"
    VOLUNTEER = "volunteer"


class SeniorityLevel(StrEnum):
    ENTRY = "entry"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    MANAGER = "manager"


class RoleCategory(StrEnum):
    DATA_ENGINEER = "data_engineer"
    SOFTWARE_ENGINEER = "software_engineer"
    DATA_SCIENTIST = "data_scientist"
    ML_ENGINEER = "ml_engineer"
    DATA_ANALYST = "data_analyst"
    ANALYTICS_ENGINEER = "analytics_engineer"
    PLATFORM_ENGINEER = "platform_engineer"
    DEVOPS_ENGINEER = "devops_engineer"
    ENGINEERING_MANAGER = "engineering_manager"
    PRODUCT_MANAGER = "product_manager"
    OTHER = "other"


class EngagementType(StrEnum):
    DIRECT = "direct"
    CONSULTING = "consulting"
    UNKNOWN = "unknown"


class CompanyType(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    STARTUP = "startup"
    CONSULTING_FIRM = "consulting_firm"
    ENTERPRISE = "enterprise"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Salary (always normalized to annual EUR)
# ---------------------------------------------------------------------------

class Salary(TypedDict):
    min_annual_eur: float | None   # null if not disclosed
    max_annual_eur: float | None
    currency_original: str         # e.g. "EUR", "USD" — what the board reported
    frequency_original: str        # "yearly" | "monthly" | "daily" | "hourly"
    is_disclosed: bool             # true if min or max is non-null


# ---------------------------------------------------------------------------
# Company info (pre-enriched where available)
# ---------------------------------------------------------------------------

class CompanyInfo(TypedDict):
    name: str
    industry: list[str]                 # e.g. ["FinTech", "Financial Services"]
    size_employees: int | None
    year_founded: int | None
    hq_country: str | None              # ISO 3166-1 alpha-2 e.g. "FR"
    org_type: CompanyType
    stock_symbol: str | None
    stock_exchange: str | None
    latest_funding_type: str | None     # "Series A", "Series B", etc.
    latest_funding_amount_usd: int | None
    homepage_url: str | None


# ---------------------------------------------------------------------------
# Enrichment tracking
# ---------------------------------------------------------------------------

class EnrichmentStatus(TypedDict):
    tech_extracted: bool     # technology list populated
    company_researched: bool # company_info enriched beyond board data
    vertical_classified: bool  # engagement_type + end_client populated
    translated: bool         # description_text is in English
    scored: bool             # scores computed


# ---------------------------------------------------------------------------
# Score dimensions
# ---------------------------------------------------------------------------

class Scores(TypedDict):
    pay: float
    flexibility: float
    low_responsibility: float
    tech_match: float
    company_quality: float


# ---------------------------------------------------------------------------
# The canonical job record
# ---------------------------------------------------------------------------

class CanonicalJob(TypedDict):
    # --- Identity ---
    id: str                          # unique, stable, board-scoped
    source_board: str                # "hiringcafe" | "freework" | ...
    source_url: str | None           # permalink on the source board

    # --- Core ---
    title: str
    company: str                     # posting company display name
    apply_url: str | None            # direct link to ATS application
    location_raw: str                # as-provided location string (e.g. "Paris, Île-de-France")
    workplace_type: WorkplaceType | None
    date_posted: str | None          # ISO 8601 date

    # --- Compensation ---
    salary: Salary

    # --- Role ---
    contract_types: list[ContractType]
    seniority_level: SeniorityLevel | None
    role_category: RoleCategory | None
    years_experience_min: int | None

    # --- Skills ---
    technologies: list[str]          # e.g. ["Python", "Spark", "AWS"]
    competencies: list[str]          # non-tech skills e.g. ["data modeling", "stakeholder management"]

    # --- Content ---
    description_text: str            # full job description
    description_language: str        # ISO 639-1 e.g. "en", "fr"

    # --- Company (pre-enriched where source provides it) ---
    company_info: CompanyInfo

    # --- Engagement ---
    engagement_type: EngagementType   # direct employer or via consulting firm?
    posting_company_type: str | None  # "end_client" | "esn" | "startup" | "unknown"
    end_client_name: str | None       # if engagement_type=consulting
    end_client_sector: str | None     # e.g. "banking", "energy"

    # --- Contract details ---
    contract_duration: str | None     # free-form e.g. "6 months", "12 mois"

    # --- Metrics (from source board) ---
    views: int | None
    applications: int | None
    is_expired: bool

    # --- Pipeline state ---
    _enrichment: EnrichmentStatus

    # --- Scores (populated by scoring stage) ---
    scores: Scores | None
    overall_score: float | None
    recommendation_tier: NotRequired[str]  # "top" | "high" | "medium" | "low"

    # --- Raw source (for debugging) ---
    _source: dict                     # the original record from the board


# ---------------------------------------------------------------------------
# Factory: create a skeleton job with sensible defaults
# ---------------------------------------------------------------------------

def new_canonical_job(source_board: str) -> CanonicalJob:
    """Return a skeleton CanonicalJob with all defaults filled in."""
    return CanonicalJob(
        id="",
        source_board=source_board,
        source_url=None,
        title="",
        company="",
        apply_url=None,
        location_raw="",
        workplace_type=None,
        date_posted=None,
        salary=Salary(
            min_annual_eur=None,
            max_annual_eur=None,
            currency_original="EUR",
            frequency_original="yearly",
            is_disclosed=False,
        ),
        contract_types=[],
        seniority_level=None,
        role_category=None,
        years_experience_min=None,
        technologies=[],
        competencies=[],
        description_text="",
        description_language="en",
        company_info=CompanyInfo(
            name="",
            industry=[],
            size_employees=None,
            year_founded=None,
            hq_country=None,
            org_type=CompanyType.UNKNOWN,
            stock_symbol=None,
            stock_exchange=None,
            latest_funding_type=None,
            latest_funding_amount_usd=None,
            homepage_url=None,
        ),
        engagement_type=EngagementType.UNKNOWN,
        posting_company_type=None,
        end_client_name=None,
        end_client_sector=None,
        contract_duration=None,
        views=None,
        applications=None,
        is_expired=False,
        _enrichment=EnrichmentStatus(
            tech_extracted=False,
            company_researched=False,
            vertical_classified=False,
            translated=False,
            scored=False,
        ),
        scores=None,
        overall_score=None,
        _source={},
    )
