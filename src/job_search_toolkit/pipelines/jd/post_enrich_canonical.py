"""
LLM enrichment for queued LinkedIn recruiter posts.

Posts whose regex verdict is ``queue`` land in ``silver.jobs`` with an empty
``title`` and ``location_raw`` (the deterministic extractor couldn't
confidently fit them). This module fills those gaps — plus role category,
seniority, engagement, and end-client details — with one LLM call per queued
post, mirroring ``enrich_canonical``.

A post the LLM genuinely cannot fill (the text contains no role/location
signal) keeps an empty title/location and is never fabricated. The row is
still marked processed (``_enrichment["post_enriched"]``) so it is not
retried.
"""

from __future__ import annotations

import logging

from .resources.llm_client import LLMClient

logger = logging.getLogger(__name__)

POST_ENRICH_SYSTEM = """You are a technical recruiter analyzing a LinkedIn recruiter post.
Extract the role title and location from the post text, plus the role category,
seniority, engagement, and end-client details.
If the post genuinely does not specify a title or a location, leave those two
fields as empty strings — do NOT invent values that are not in the post.
Output ONLY the JSON object with these keys:
- title: the role title, or "" if not stated
- location_raw: the location, or "" if not stated
- role_category: one of data_engineer, data_analyst, data_scientist, ml_engineer,
  analytics_engineer, platform_engineer, devops_engineer, software_engineer,
  product_manager, other, or null
- seniority_level: entry, junior, mid, senior, lead, manager, or null
- engagement_type: direct, consulting, or unknown
- end_client_name: the end client company if the post names one, else null
- end_client_sector: banking, insurance, fintech, telecom, energy, healthcare,
  luxury, retail, aerospace_defense, automotive, consulting, public_sector,
  other, or null
- competencies: a list of non-technical skills and domain knowledge, empty if none"""


def _build_prompt(row: dict) -> str:
    """Build one enrichment prompt per queued post, carrying the post text."""
    company = row.get("company", "")
    desc = row.get("description_text", "") or ""
    return (
        f"LinkedIn post by {company or 'unknown'}:\n\n"
        f"{desc[:3000]}"
    )


def _clean(value: object) -> str | None:
    """Return a stripped non-empty string, else None."""
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return None


def _apply_result(row: dict, result: dict) -> None:
    """Write one LLM result onto a queued post row, mutating it in place.

    Pre: ``row`` is a queued ``linkedin_posts`` row whose ``title`` and/or
    ``location_raw`` are empty. ``result`` is the parsed JSON from one LLM
    call.
    Post: title/location are filled when the LLM provided a value (never
    fabricated); when the LLM genuinely cannot determine them they are set to
    ``"unknown"`` (a non-empty terminal sentinel, consistent with the
    warehouse's ``org_type = 'unknown'`` convention) so the empty-based gate
    never re-selects the row. The remaining enrichment fields are written from
    the result; the row is marked processed via ``_enrichment["post_enriched"]``
    (a within-run flag distinguishing LLM success from transient failure).
    """
    title = _clean(result.get("title"))
    row["title"] = title if title else "unknown"
    location = _clean(result.get("location_raw"))
    row["location_raw"] = location if location else "unknown"
    row["role_category"] = _clean(result.get("role_category"))
    row["seniority_level"] = _clean(result.get("seniority_level"))
    engagement = _clean(result.get("engagement_type"))
    if engagement:
        row["engagement_type"] = engagement
    row["end_client_name"] = _clean(result.get("end_client_name"))
    row["end_client_sector"] = _clean(result.get("end_client_sector"))
    competencies = result.get("competencies")
    row["competencies"] = (
        [c for c in competencies if isinstance(c, str) and c.strip()]
        if isinstance(competencies, list)
        else []
    )
    row.setdefault("_enrichment", {})["post_enriched"] = True


async def enrich_posts(rows: list[dict], client: LLMClient) -> None:
    """Fill title/location and role details for queued posts via the LLM.

    Pre: each ``row`` is a queued ``linkedin_posts`` row (empty ``title``
    and/or ``location_raw``) carrying ``id``, ``source_board``, ``company``
    and ``description_text``. ``client`` is a live ``LLMClient`` (the caller
    owns closing it).
    Post: one prompt is built per row (including the post text), all prompts
    are sent through ``client.batch_complete_json`` in one batched call, and
    each row is mutated in place with its parsed result. Rows the LLM cannot
    fill keep an empty title/location (never fabricated) and are still marked
    processed.
    """
    if not rows:
        return
    prompts = [_build_prompt(row) for row in rows]
    results = await client.batch_complete_json(
        prompts,
        system=POST_ENRICH_SYSTEM,
        temperature=0.2,
        max_tokens=600,
    )
    for row, result in zip(rows, results):
        _apply_result(row, result)
    logger.info("Enriched %d linkedin_posts rows", len(rows))
