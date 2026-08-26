"""
Adapters: normalize LinkedIn job listings and recruiter posts into the
canonical job schema.

Two LinkedIn artifact kinds both signal job opportunities and each maps to its
own board:

- ``linkedin_jobs`` — structured JSON-LD job listings (``JobRecord``). Most
  fields map deterministically; ``employment_type`` drives both
  ``contract_types`` and ``workplace_type``.
- ``linkedin_posts`` — unstructured recruiter posts (``PostRecord``). The
  structured fields (url, author, date, text, technologies) map directly,
  while role/location/salary etc. come from the deterministic regex extractor
  ``extract_from_post``. A post whose regex verdict is ``drop`` (no job signal)
  yields ``None`` and is excluded from ``silver.jobs``.
"""

from typing import cast

from job_search_toolkit.scrapers.linkedin.models import JobRecord, PostRecord
from job_search_toolkit.scrapers.linkedin.post_extract import extract_from_post
from job_search_toolkit.schemas import (
    CanonicalJob,
    ContractType,
    WorkplaceType,
    new_canonical_job,
)

# JSON-LD JobPosting employmentType -> ContractType.
# LinkedIn emits the standard schema.org values (uppercase, snake_case).
CONTRACT_FROM_EMPLOYMENT_TYPE: dict[str, ContractType] = {
    "FULL_TIME": ContractType.FULL_TIME,
    "PART_TIME": ContractType.PART_TIME,
    "CONTRACTOR": ContractType.CONTRACT,
    "TEMPORARY": ContractType.TEMPORARY,
    "INTERNSHIP": ContractType.INTERNSHIP,
    "VOLUNTEER": ContractType.VOLUNTEER,
}

# Standard JSON-LD employmentType has no remote/hybrid/onsite concept; when a
# listing folds one of these tokens into the value we honor it, else workplace
# stays None (never fabricated).
_WORKPLACE_KEYWORDS: list[tuple[str, WorkplaceType]] = [
    ("remote", WorkplaceType.REMOTE),
    ("hybrid", WorkplaceType.HYBRID),
    ("on-site", WorkplaceType.ONSITE),
    ("onsite", WorkplaceType.ONSITE),
    ("on site", WorkplaceType.ONSITE),
]


def _workplace_from_employment_type(value: str | None) -> WorkplaceType | None:
    """Map an employment_type string to a WorkplaceType, or None.

    Pre: ``value`` is a raw employment_type string or None.
    Post: returns a WorkplaceType when the value carries a remote/hybrid/onsite
    token (case-insensitive, longest-first), else None.
    """
    if not value:
        return None
    low = value.lower()
    for token, workplace in _WORKPLACE_KEYWORDS:
        if token in low:
            return workplace
    return None


def _contract_from_employment_type(value: str | None) -> list[ContractType]:
    """Map an employment_type string to contract types, possibly empty.

    Pre: ``value`` is a raw employment_type string or None.
    Post: returns ``[mapped]`` for a recognized JSON-LD value (deduped, so at
    most one), else ``[]``. Never raises on absent/unknown input.
    """
    if not value:
        return []
    contract = CONTRACT_FROM_EMPLOYMENT_TYPE.get(value.upper())
    return [contract] if contract is not None else []


def _compose_location(loc: dict | None) -> str:
    """Join non-empty locality/country parts into a location_raw string.

    Pre: ``loc`` is a ``Location`` dict (or None / partial).
    Post: returns ``"locality, country"`` using only non-empty parts, or ``""``
    when neither is present.
    """
    loc = loc or {}
    parts = [p for p in (loc.get("locality"), loc.get("country")) if p]
    return ", ".join(parts)


def normalize_linkedin_job(job: JobRecord) -> CanonicalJob:
    """Convert a LinkedIn ``JobRecord`` into a ``CanonicalJob``.

    Pre: ``job`` is a ``JobRecord`` with at least ``job_url``; optional fields
    (``employment_type``, ``date_posted``, ``location``) may be None.
    Post: returns a ``CanonicalJob`` for board ``linkedin_jobs`` with identity,
    core, contract and workplace fields populated; ``_source`` carries the
    original record. Missing fields become None / empty, never crash.
    """
    job_url = job.get("job_url", "")
    job_out = new_canonical_job("linkedin_jobs")
    job_out.update({
        "id": job_url,
        "source_url": job_url,
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "apply_url": job_url,
        "location_raw": _compose_location(cast(dict | None, job.get("location"))),
        "workplace_type": _workplace_from_employment_type(job.get("employment_type")),
        "date_posted": job.get("date_posted"),
        "contract_types": _contract_from_employment_type(job.get("employment_type")),
        "technologies": job.get("technologies", []),
        "description_text": job.get("description", ""),
        "description_language": "en",
        "_source": job,
    })
    return job_out


def normalize_linkedin_post(post: PostRecord) -> CanonicalJob | None:
    """Convert a LinkedIn ``PostRecord`` into a ``CanonicalJob`` or ``None``.

    Pre: ``post`` is a ``PostRecord`` with ``post_url``, ``author_name`` and
    ``text``; ``date_published`` may be None.
    Post: delegates role/location/salary extraction to ``extract_from_post``.
    Returns ``None`` when the verdict is ``drop`` (no job signal). For
    ``land`` the extracted title/location are used; for ``queue`` title and
    location_raw are left empty (filled later by the LLM pass) while every
    other extracted field is carried over. The recruiter/poster identity
    (``poster_name``/``poster_url``) is carried from ``author_name`` /
    ``author_profile_url``, while ``poster_location`` stays None until the
    deferred profile-scrape enrichment. ``_source`` carries the original
    record; board is ``linkedin_posts``.
    """
    extraction = extract_from_post(post)
    verdict = extraction["verdict"]
    if verdict == "drop":
        return None

    is_queue = verdict == "queue"
    post_url = post.get("post_url", "")
    post_out = new_canonical_job("linkedin_posts")
    post_out.update({
        "id": post_url,
        "source_url": post_url,
        "title": "" if is_queue else (extraction["title"] or ""),
        "company": post.get("author_name", ""),
        "apply_url": post_url,
        "location_raw": "" if is_queue else (extraction["location_raw"] or ""),
        "workplace_type": extraction["workplace_type"],
        "date_posted": post.get("date_published"),
        "contract_types": extraction["contract_types"],
        "seniority_level": extraction["seniority_level"],
        "years_experience_min": extraction["years_experience_min"],
        "technologies": post.get("technologies", []),
        "description_text": post.get("text", ""),
        "description_language": extraction["description_language"],
        "engagement_type": extraction["engagement_type"],
        "end_client_name": extraction["end_client_name"],
        "contract_duration": extraction["contract_duration"],
        # Recruiter/poster identity comes straight from the post; the poster's
        # location is filled later by the deferred profile-scrape enrichment.
        "poster_name": post.get("author_name"),
        "poster_url": post.get("author_profile_url"),
        "poster_location": None,
        "_source": post,
    })
    # Salary is a nested dict; the factory default (undisclosed) stands when
    # the extractor found none.
    if extraction["salary"] is not None:
        post_out["salary"] = extraction["salary"]
    return post_out
