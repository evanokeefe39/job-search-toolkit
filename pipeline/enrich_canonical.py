"""
Canonical enrichment for free-work jobs using instructor + DeepSeek.

Each function works on canonical-format jobs, reads from and writes to
canonical fields, and uses Pydantic models for structured LLM output.
Idempotent — checks _enrichment flags and skips already-processed jobs.

Uses instructor (pydantic-based structured LLM output) with the OpenAI SDK
against DeepSeek's OpenAI-compatible endpoint.
"""

from __future__ import annotations

import logging

from openai import OpenAI
from pydantic import BaseModel, Field

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from schemas import CompanyType

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        import instructor
        _client = instructor.from_openai(OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
        ))
    return _client


# ---------------------------------------------------------------------------
# Pydantic models for structured LLM output
# ---------------------------------------------------------------------------

class TranslationOutput(BaseModel):
    english_text: str = Field(description="The English translation")


class TechExtractionOutput(BaseModel):
    technologies: list[str] = Field(description="Specific technologies, tools, platforms, frameworks, languages (e.g. 'Apache Spark', 'GCP', 'Snowflake')")
    competencies: list[str] = Field(description="Non-technical skills and domain knowledge (e.g. 'data modeling', 'stakeholder management')")
    seniority_level: str | None = Field(description="One of: entry, junior, mid, senior, lead, manager, or null if unclear")
    role_category: str | None = Field(description="One of: data_engineer, data_analyst, data_scientist, ml_engineer, analytics_engineer, platform_engineer, devops_engineer, software_engineer, product_manager, other, or null if unclear")


class ClassificationOutput(BaseModel):
    posting_company_type: str = Field(description="One of: end_client, esn, startup, unknown")
    end_client_name: str | None = Field(description="Name of the actual client company, if different from posting company")
    end_client_sector: str | None = Field(description="Sector: banking, insurance, fintech, telecom, energy, healthcare, luxury, retail, aerospace_defense, automotive, consulting, public_sector, other")
    engagement_type: str = Field(description="One of: direct, consulting, unknown")


class CompanyResearchOutput(BaseModel):
    company_type: str = Field(description="One of: public, private, startup, consulting_firm, enterprise, unknown")
    employee_count: int | None = Field(description="Approximate number of employees")
    is_public: bool = Field(description="Whether the company is publicly traded")
    info_quality: str = Field(description="One of: high, medium, low")


# ---------------------------------------------------------------------------
# Enrichment functions
# ---------------------------------------------------------------------------

TRANSLATE_SYSTEM = """You are a technical translator specializing in French IT job descriptions.
Translate the following French text to English. Preserve technical terms, tool names,
and company names exactly as they appear. Keep the same level of detail and tone.
Output ONLY the English translation."""

EXTRACT_SYSTEM = """You are a technical recruiter analyzing job descriptions.
Extract structured information from the job description below.
- technologies: specific tools, platforms, frameworks, languages mentioned
- competencies: non-technical skills and domain knowledge
- seniority_level: based on years of experience, title keywords, and responsibility level
- role_category: the closest match for the role type
Output ONLY the JSON object."""

CLASSIFY_SYSTEM = """You are analyzing French IT job descriptions to classify the industry
of the END CLIENT (not the consulting firm that posted the ad, if applicable).
The posting company is often a consulting/ESN firm. The actual client is described
in the job text. Output ONLY the JSON object."""

RESEARCH_SYSTEM = """You are a business analyst. Based on the company name and any
context provided, estimate the company's size, type, and whether it's publicly traded.
Output ONLY the JSON object."""


from concurrent.futures import ThreadPoolExecutor

_MAX_WORKERS = 5  # bounded by LLM_CONCURRENCY for polite API usage


def _process_pool(items: list, fn) -> None:
    """Run fn on each item in parallel, bounded by _MAX_WORKERS."""
    if not items:
        return
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        list(pool.map(fn, items))


def translate_jobs(jobs: list[dict]) -> list[dict]:
    """Translate French descriptions to English. Idempotent, concurrent."""
    client = _get_client()
    to_process = []
    for job in jobs:
        enrichment = job.setdefault("_enrichment", {})
        if enrichment.get("translated", False):
            continue
        if job.get("description_language") == "en":
            enrichment["translated"] = True
            continue
        desc = job.get("description_text", "")
        if not desc.strip():
            enrichment["translated"] = True
            continue
        to_process.append(job)

    def _do(job: dict) -> None:
        try:
            result = client.chat.completions.create(
                model=LLM_MODEL, response_model=TranslationOutput,
                messages=[{"role": "system", "content": TRANSLATE_SYSTEM},
                          {"role": "user", "content": job["description_text"][:3000]}],
                max_tokens=2000,
            )
            job["description_text"] = result.english_text
            job["description_language"] = "en"
            job["_enrichment"]["translated"] = True
        except Exception as e:
            logger.warning("Translation failed: %s", e)

    _process_pool(to_process, _do)
    logger.info("Translated %d jobs", len(to_process))
    return jobs


def extract_tech(jobs: list[dict]) -> list[dict]:
    """Extract technologies, competencies, seniority, role. Concurrent."""
    client = _get_client()
    to_process = []
    for job in jobs:
        enrichment = job.setdefault("_enrichment", {})
        if enrichment.get("tech_extracted", False):
            continue
        to_process.append(job)

    def _do(job: dict) -> None:
        try:
            desc = job.get("description_text", "")
            title = job.get("title", "")
            result = client.chat.completions.create(
                model=LLM_MODEL, response_model=TechExtractionOutput,
                messages=[{"role": "system", "content": EXTRACT_SYSTEM},
                          {"role": "user", "content": f"Title: {title}\n\nDescription: {desc[:3000]}"}],
                max_tokens=1000,
            )
            job["technologies"] = result.technologies
            job["competencies"] = result.competencies
            job["seniority_level"] = result.seniority_level
            job["role_category"] = result.role_category
            job["_enrichment"]["tech_extracted"] = True
        except Exception as e:
            logger.warning("Tech extraction failed: %s", e)

    _process_pool(to_process, _do)
    logger.info("Tech extracted for %d jobs", len(to_process))
    return jobs


def classify_jobs(jobs: list[dict]) -> list[dict]:
    """Classify company vertical and engagement. Concurrent."""
    client = _get_client()
    to_process = []
    for job in jobs:
        enrichment = job.setdefault("_enrichment", {})
        if enrichment.get("vertical_classified", False):
            continue
        if job.get("source_board") == "hiringcafe":
            enrichment["vertical_classified"] = True
            continue
        to_process.append(job)

    def _do(job: dict) -> None:
        try:
            desc = job.get("description_text", "")
            company = job.get("company", "")
            result = client.chat.completions.create(
                model=LLM_MODEL, response_model=ClassificationOutput,
                messages=[{"role": "system", "content": CLASSIFY_SYSTEM},
                          {"role": "user", "content": f"Posting company: {company}\n\nDescription: {desc[:3000]}"}],
                max_tokens=500,
            )
            job["posting_company_type"] = result.posting_company_type
            job["end_client_name"] = result.end_client_name
            job["end_client_sector"] = result.end_client_sector
            job["engagement_type"] = result.engagement_type
            job["_enrichment"]["vertical_classified"] = True
        except Exception as e:
            logger.warning("Classification failed: %s", e)

    _process_pool(to_process, _do)
    logger.info("Classified %d jobs", len(to_process))
    return jobs


def enrich_company_stats(jobs: list[dict]) -> list[dict]:
    """Research company stats. Concurrent."""
    client = _get_client()
    to_process = []
    for job in jobs:
        enrichment = job.setdefault("_enrichment", {})
        if enrichment.get("company_researched", False):
            continue
        if job.get("source_board") == "hiringcafe":
            enrichment["company_researched"] = True
            continue
        to_process.append(job)

    def _do(job: dict) -> None:
        try:
            ci = job.get("company_info", {})
            company = ci.get("name") or job.get("company", "")
            sector = job.get("end_client_sector", "")
            result = client.chat.completions.create(
                model=LLM_MODEL, response_model=CompanyResearchOutput,
                messages=[{"role": "system", "content": RESEARCH_SYSTEM},
                          {"role": "user", "content": f"Company: {company}\nSector: {sector}"}],
                max_tokens=300,
            )
            ci["org_type"] = _map_company_type(result.company_type)
            ci["name"] = ci.get("name") or company
            job["_enrichment"]["company_researched"] = True
        except Exception as e:
            logger.warning("Company research failed: %s", e)

    _process_pool(to_process, _do)
    logger.info("Researched %d companies", len(to_process))
    return jobs


def _map_company_type(raw: str) -> str:
    mapping: dict[str, str] = {
        "public": CompanyType.PUBLIC,
        "private": CompanyType.PRIVATE,
        "startup": CompanyType.STARTUP,
        "consulting_firm": CompanyType.CONSULTING_FIRM,
        "enterprise": CompanyType.ENTERPRISE,
        "unknown": CompanyType.UNKNOWN,
    }
    return mapping.get(raw.lower(), CompanyType.UNKNOWN)
