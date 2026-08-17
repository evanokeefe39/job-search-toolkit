"""LinkedIn source adapter record schemas.

These are the adapter's own structured records — distinct from the pipeline's
``CanonicalJob``. They are the candidate-pool artifacts that sit behind the
human gate: posts feed the recruiter rolodex, jobs feed the JD pool. Nothing
here flows into Twenty or the silver warehouse without human shortlisting.

See ``tasks/plans/linkedin-source-adapter.md`` for the build spec.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# "full"  — parsed from the page's JSON-LD block.
# "partial" — fell back to a search snippet or og:description (login wall /
#             missing JSON-LD); fields may be truncated or absent.
ContentQuality = Literal["full", "partial"]


class Location(TypedDict):
    country: str | None   # ISO 3166-1 alpha-2, e.g. "FR"
    locality: str | None  # e.g. "Lille et périphérie"


class PostRecord(TypedDict):
    post_url: str
    author_name: str
    author_profile_url: str | None
    date_published: str | None       # ISO 8601
    text: str                        # full articleBody text
    technologies: list[str]          # canonical tech keywords, deduped
    likes: int | None
    activity_id: str | None          # trailing digits of -activity-<id>
    name_from_slug: bool             # author name derived from vanity slug
    content_quality: ContentQuality


class JobRecord(TypedDict):
    job_url: str
    job_id: str                      # trailing digits of /jobs/view/...-<id>
    title: str
    company: str                     # hiringOrganization.name
    company_url: str | None          # hiringOrganization.sameAs
    location: Location
    employment_type: str | None      # raw JobPosting value, e.g. "FULL_TIME"
    date_posted: str | None          # ISO 8601
    description: str                 # full JD text (HTML stripped)
    technologies: list[str]          # canonical tech keywords, deduped
    content_quality: ContentQuality
