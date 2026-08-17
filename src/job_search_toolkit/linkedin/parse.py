"""Extract JSON-LD and build PostRecord / JobRecord from LinkedIn pages.

Parse strategy: read the page's embedded ``application/ld+json`` blocks
(``SocialMediaPosting`` / ``Article`` for posts, ``JobPosting`` for jobs) and
map them onto the shared record contracts. When the block is missing or empty
(login-wall / snippet variant), fall back to ``og:*`` meta tags and flag
``content_quality="partial"``.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from bs4 import BeautifulSoup

from job_search_toolkit.linkedin.models import ContentQuality, JobRecord, Location, PostRecord
from job_search_toolkit.linkedin.urls import (
    author_slug_from_post_url,
    job_id_from_url,
    post_activity_id,
)

_WHITESPACE_RE = re.compile(r"\s+")
_POST_TYPES = frozenset({"SocialMediaPosting", "Article", "BlogPosting"})
_JOB_TYPES = frozenset({"JobPosting"})


def extract_jsonld(html_text: str) -> list[dict[str, Any]]:
    """Extract every JSON-LD block from an HTML document, in document order.

    Pre: ``html_text`` is the text of an HTML page.
    Post: returns one parsed dict per ``<script type="application/ld+json">``
    element that contains valid JSON (list-valued payloads are flattened into
    their dict entries); malformed scripts are skipped.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    blocks: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            blocks.append(data)
        elif isinstance(data, list):
            blocks.extend(item for item in data if isinstance(item, dict))
    return blocks


def _find_jsonld_block(blocks: list[dict[str, Any]], types: frozenset[str]) -> dict[str, Any] | None:
    """Return the first JSON-LD block whose ``@type`` is in ``types``.

    Pre: ``blocks`` is the output of :func:`extract_jsonld`.
    Post: returns the first dict (in document order) whose ``@type`` — a string
    or a list of strings — intersects ``types``; each ``@graph`` list inside a
    block is searched too. ``None`` when nothing matches.
    """
    for block in blocks:
        if _type_matches(block, types):
            return block
        graph = block.get("@graph")
        if isinstance(graph, list):
            for entry in graph:
                if isinstance(entry, dict) and _type_matches(entry, types):
                    return entry
    return None


def _type_matches(block: dict[str, Any], types: frozenset[str]) -> bool:
    """True when the block's ``@type`` (string or list) intersects ``types``."""
    declared = block.get("@type")
    if isinstance(declared, str):
        return declared in types
    if isinstance(declared, list):
        return any(isinstance(item, str) and item in types for item in declared)
    return False


def parse_post(html_text: str, post_url: str) -> PostRecord:
    """Build a ``PostRecord`` from a LinkedIn post page.

    Pre: ``html_text`` is the fetched body of the post page; ``post_url`` is
    its canonical URL (used for ``activity_id`` and the author slug).
    Post: with a non-empty ``articleBody`` in the JSON-LD block the record is
    ``content_quality="full"``; otherwise it falls back to ``og:description``
    with ``content_quality="partial"``. An empty author name is filled from the
    vanity slug (title-cased, ``name_from_slug=True``) when the URL has one.
    """
    blocks = extract_jsonld(html_text)
    block = _find_jsonld_block(blocks, _POST_TYPES)

    if block is not None and (block.get("articleBody") or ""):
        text = str(block["articleBody"])
        author_name, author_profile_url = _author_from_block(block)
        date_published = _optional_str(block.get("datePublished"))
        likes = _likes_from_interaction_statistic(block.get("interactionStatistic"))
        quality: ContentQuality = "full"
    else:
        text = _og_meta(html_text, "description") or ""
        author_name = ""
        author_profile_url = None
        date_published = None
        likes = None
        quality = "partial"

    activity_id = post_activity_id(post_url)
    slug = author_slug_from_post_url(post_url)
    name_from_slug = False
    if not author_name and slug:
        author_name = _titlecase_slug(slug)
        author_profile_url = "https://www.linkedin.com/in/" + slug
        name_from_slug = True

    return PostRecord(
        post_url=post_url,
        author_name=author_name,
        author_profile_url=author_profile_url,
        date_published=date_published,
        text=text,
        technologies=[],
        likes=likes,
        activity_id=activity_id,
        name_from_slug=name_from_slug,
        content_quality=quality,
    )


def parse_job(html_text: str, job_url: str) -> JobRecord:
    """Build a ``JobRecord`` from a LinkedIn job page.

    Pre: ``html_text`` is the fetched body of the job page; ``job_url`` is its
    canonical URL (used for ``job_id``).
    Post: with a non-empty ``title`` in the JobPosting JSON-LD block the record
    is ``content_quality="full"`` (description is HTML-stripped, whitespace
    collapsed); otherwise it falls back to ``og:title`` / ``og:description``
    with ``content_quality="partial"`` and empty company/location fields.
    """
    blocks = extract_jsonld(html_text)
    block = _find_jsonld_block(blocks, _JOB_TYPES)

    if block is not None and (block.get("title") or ""):
        title = str(block["title"])
        date_posted = _optional_str(block.get("datePosted"))
        employment_type = _optional_str(block.get("employmentType"))
        company, company_url = _company_from_block(block.get("hiringOrganization"))
        location = _location_from_block(block.get("jobLocation"))
        raw_description = str(block.get("description") or "")
        description = _strip_html(html.unescape(raw_description))
        quality: ContentQuality = "full"
    else:
        title = _og_meta(html_text, "title") or ""
        company = ""
        company_url = None
        location = {"country": None, "locality": None}
        description = _og_meta(html_text, "description") or ""
        date_posted = None
        employment_type = None
        quality = "partial"

    job_id = job_id_from_url(job_url) or ""

    return JobRecord(
        job_url=job_url,
        job_id=job_id,
        title=title,
        company=company,
        company_url=company_url,
        location=location,
        employment_type=employment_type,
        date_posted=date_posted,
        description=description,
        technologies=[],
        content_quality=quality,
    )


def _author_from_block(block: dict[str, Any]) -> tuple[str, str | None]:
    """Extract ``(name, profile_url)`` from a post block's ``author`` dict."""
    author = block.get("author")
    if not isinstance(author, dict):
        return "", None
    name = str(author.get("name") or "")
    url = author.get("url")
    return name, str(url) if url else None


def _company_from_block(org: Any) -> tuple[str, str | None]:
    """Extract ``(name, sameAs_url)`` from a JobPosting ``hiringOrganization``."""
    if not isinstance(org, dict):
        return "", None
    name = str(org.get("name") or "")
    same_as = org.get("sameAs")
    return name, str(same_as) if same_as else None


def _location_from_block(job_location: Any) -> Location:
    """Extract ``{country, locality}`` from a JobPosting ``jobLocation``."""
    if not isinstance(job_location, dict):
        return {"country": None, "locality": None}
    address = job_location.get("address")
    if not isinstance(address, dict):
        return {"country": None, "locality": None}
    country = address.get("addressCountry")
    locality = address.get("addressLocality")
    return {
        "country": str(country) if country else None,
        "locality": str(locality) if locality else None,
    }


def _likes_from_interaction_statistic(value: Any) -> int | None:
    """Extract the Like count from ``interactionStatistic`` (list or single dict).

    Post: the integer ``userInteractionCount`` of the first entry whose
    ``interactionType`` contains "like" (case-insensitive), or ``None`` when no
    such entry exists or its count is not an int.
    """
    entries = value if isinstance(value, list) else [value]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        interaction_type = entry.get("interactionType")
        if not interaction_type or "like" not in str(interaction_type).lower():
            continue
        try:
            return int(entry.get("userInteractionCount"))
        except (TypeError, ValueError):
            return None
    return None


def _strip_html(raw: str) -> str:
    """Strip HTML tags from ``raw``, joining text with spaces and collapsing whitespace."""
    text = BeautifulSoup(raw, "html.parser").get_text(" ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _og_meta(html_text: str, name: str) -> str | None:
    """Return the ``content`` of the ``<meta property="og:<name>">`` tag, if any."""
    soup = BeautifulSoup(html_text, "html.parser")
    tag = soup.find("meta", attrs={"property": f"og:{name}"})
    if tag is None:
        tag = soup.find("meta", attrs={"name": f"og:{name}"})
    if tag is None or not tag.get("content"):
        return None
    return str(tag["content"])


def _titlecase_slug(slug: str) -> str:
    """Best-effort title-casing of a vanity slug: split on ``-``/space, capitalize, join."""
    words = re.split(r"[- ]", slug)
    return " ".join(word.capitalize() for word in words if word)


def _optional_str(value: Any) -> str | None:
    """Return ``str(value)`` or ``None`` when ``value`` is ``None``."""
    return str(value) if value is not None else None
