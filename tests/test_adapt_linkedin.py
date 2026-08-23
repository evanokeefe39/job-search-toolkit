"""Tests for the LinkedIn -> canonical job normalizers (adapt_linkedin.py).

Covers both boards: ``linkedin_jobs`` (structured ``JobRecord``) and
``linkedin_posts`` (unstructured ``PostRecord``, delegating role/location
extraction to ``extract_from_post`` with verdict-based handling).
"""

from __future__ import annotations

from job_search_toolkit.linkedin.models import JobRecord, PostRecord
from job_search_toolkit.pipelines.jd.adapt_linkedin import (
    normalize_linkedin_job,
    normalize_linkedin_post,
)
from job_search_toolkit.schemas import ContractType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

JOB_URL = "https://www.linkedin.com/jobs/view/1234567890"


def make_job(**overrides: object) -> JobRecord:
    """Return a realistic full ``JobRecord`` with overrides applied."""
    base: JobRecord = {
        "job_url": JOB_URL,
        "job_id": "1234567890",
        "title": "Senior Data Engineer",
        "company": "Acme Corp",
        "company_url": "https://acme.example.com",
        "location": {"country": "FR", "locality": "Paris"},
        "employment_type": "FULL_TIME",
        "date_posted": "2026-08-01",
        "description": "Build and scale the data platform.",
        "technologies": ["Python", "Spark"],
        "content_quality": "full",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def make_post(text: str, **overrides: object) -> PostRecord:
    """Return a ``PostRecord`` carrying ``text`` (plus overrides)."""
    base: PostRecord = {
        "post_url": "https://www.linkedin.com/posts/alice_hi-activity-7488545218842",
        "author_name": "Alice Recruiter",
        "author_profile_url": "https://www.linkedin.com/in/alice",
        "date_published": "2026-08-02",
        "text": text,
        "technologies": ["Python", "Spark"],
        "likes": 12,
        "activity_id": "7488545218842",
        "name_from_slug": False,
        "content_quality": "full",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base

# ---------------------------------------------------------------------------
# linkedin_jobs board
# ---------------------------------------------------------------------------


def test_job_full_populates_core_fields():
    job = make_job()

    out = normalize_linkedin_job(job)

    assert out["id"] == JOB_URL
    assert out["source_board"] == "linkedin_jobs"
    assert out["source_url"] == JOB_URL
    assert out["apply_url"] == JOB_URL
    assert out["title"] == "Senior Data Engineer"
    assert out["company"] == "Acme Corp"
    assert out["location_raw"] == "Paris, FR"
    assert out["date_posted"] == "2026-08-01"
    assert out["description_text"] == "Build and scale the data platform."
    assert out["technologies"] == ["Python", "Spark"]
    assert out["_source"] is job


def test_job_employment_type_maps_to_contract():
    job = make_job(employment_type="CONTRACTOR")

    out = normalize_linkedin_job(job)

    assert out["contract_types"] == [ContractType.CONTRACT]


def test_job_employment_type_remote_token_maps_workplace():
    job = make_job(employment_type="REMOTE")

    out = normalize_linkedin_job(job)

    assert out["workplace_type"].value == "remote"


def test_job_unknown_employment_type_is_silent():
    job = make_job(employment_type="PER_DIEM")

    out = normalize_linkedin_job(job)

    assert out["contract_types"] == []
    assert out["workplace_type"] is None


def test_job_missing_hiring_organization_does_not_crash():
    # hiringOrganization absent -> company empty, location all-null.
    job = make_job(
        company="",
        company_url=None,
        location={"country": None, "locality": None},
    )

    out = normalize_linkedin_job(job)

    assert out["company"] == ""
    assert out["location_raw"] == ""
    assert out["date_posted"] == "2026-08-01"
    assert out["source_board"] == "linkedin_jobs"


# ---------------------------------------------------------------------------
# linkedin_posts board
# ---------------------------------------------------------------------------


def test_post_land_fills_full_fields():
    post = make_post(
        "We are hiring a Senior Data Engineer in Paris with 5+ years of experience.",
    )

    out = normalize_linkedin_post(post)

    assert out is not None
    assert out["id"] == post["post_url"]
    assert out["source_board"] == "linkedin_posts"
    assert out["source_url"] == post["post_url"]
    assert out["apply_url"] == post["post_url"]
    assert out["company"] == "Alice Recruiter"
    assert out["description_text"] == post["text"]
    assert out["date_posted"] == "2026-08-02"
    assert out["technologies"] == ["Python", "Spark"]
    # The regex title may carry collapsible inner whitespace; assert on the
    # role tokens rather than an exact single-spaced string.
    assert "Senior" in out["title"] and "Data Engineer" in out["title"]
    assert "Paris" in (out["location_raw"] or "")
    assert out["_source"] is post


def test_post_land_carries_regex_fields():
    post = make_post(
        "Hiring a Senior Data Engineer in Lyon. Salary 80k€. Freelance.",
    )

    out = normalize_linkedin_post(post)

    assert out is not None
    assert out["seniority_level"].value == "senior"
    # Salary is a TypedDict (structural); check its disclosed fields directly.
    assert out["salary"]["min_annual_eur"] == 80000.0
    assert out["salary"]["max_annual_eur"] == 80000.0
    assert ContractType.CONTRACT in out["contract_types"]


def test_post_queue_leaves_title_and_location_empty():
    # A role noun with no location and no hiring-verb context -> queue.
    post = make_post("Our Data Engineer will own the data platform end to end.")

    out = normalize_linkedin_post(post)

    assert out is not None
    assert out["title"] == ""
    assert out["location_raw"] == ""
    # Other fields still carried over.
    assert out["company"] == "Alice Recruiter"
    assert out["source_board"] == "linkedin_posts"
    assert out["technologies"] == ["Python", "Spark"]


def test_post_drop_returns_none():
    # No role noun and no hiring verb -> drop.
    post = make_post("Happy Friday everyone, enjoy the weekend!")

    out = normalize_linkedin_post(post)

    assert out is None
