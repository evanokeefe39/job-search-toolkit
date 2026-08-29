"""Adversarial drafter-reviewer: one critique + one bounded revise pass.

WS3 Epic 3.2. A fresh-context reviewer LLM critiques the first-pass tailoring
against the master resume, JD, and verification report, and may propose
EXACTLY ONE targeted revision. The fabrication guard (check_fabrication) is
the ceiling: a reviewer-proposed unsupported claim is rejected and the first
pass stays intact. The loop is bounded — never more than one revise pass.
"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

import yaml

from job_search_toolkit.automation.tailor import client
from job_search_toolkit.automation.tailor.audit import check_fabrication
from job_search_toolkit.automation.tailor.merge import merge_content
from job_search_toolkit.automation.tailor.models import ReviewerResponse

REVIEWER_SYSTEM_PROMPT = """\
You are an adversarial resume reviewer. You did NOT write this draft; your
job is to find what the drafter missed or got wrong.

You receive:
- DRAFT: the first-pass tailored resume (RenderCV YAML).
- MASTER: the master resume — the ONLY source of truth for facts.
- JD: the job description.
- VERIFY: a verification report on the draft.

Critique the draft against the JD: surface SUPPORTED keywords the draft
missed — terms that appear in the JD AND are supported by the master resume
or the verification report. Flag weak framing, and anything the verify
report flags.

Rules:
- Return AT MOST ONE targeted revision — the single highest-value fix.
- NEVER invent skills, tools, metrics, employers, or claims that are not in
  the master resume. The fabrication guard is the ceiling: any unsupported
  addition is automatically rejected.
- If the draft is already good, do NOT force a revision: a "no changes"
  verdict means the first pass is accepted as-is.

Respond with your critique and, if warranted, one revision."""


@dataclass
class ReviewResult:
    """Reviewer verdict on a first-pass draft."""

    critique: str
    revision: dict | None = None
    changed: bool = False


def review_draft(
    draft_text: str,
    master_text: str,
    jd_text: str,
    verify_text: str,
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    client_kind: str = "pydantic_ai",
    temperature: float = 0.2,
    max_tokens: int = 4000,
    llm_caller: Callable | None = None,
) -> ReviewResult:
    """One fresh-context reviewer pass over the first-pass draft.

    Calls the LLM (``client.call_llm`` or an injected ``llm_caller``) with
    ``output_type=ReviewerResponse`` and maps the response to a ReviewResult.
    """
    caller = llm_caller or client.call_llm
    user = (
        f"## DRAFT (first pass)\n{draft_text}\n\n"
        f"## MASTER (source of truth)\n{master_text}\n\n"
        f"## JOB DESCRIPTION\n{jd_text}\n\n"
        f"## VERIFICATION REPORT\n{verify_text}\n"
    )
    data = asyncio.run(
        caller(
            system=REVIEWER_SYSTEM_PROMPT,
            user=user,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            client_kind=client_kind,
            temperature=temperature,
            max_tokens=max_tokens,
            output_type=ReviewerResponse,
        )
    )
    revision = data.get("revision")
    if revision is None:
        return ReviewResult(critique=data["critique"], revision=None, changed=False)
    return ReviewResult(critique=data["critique"], revision=revision, changed=True)


def apply_revision(
    original: dict,
    revision: dict,
    master_text: str,
    jd_text: str,
    merge_kwargs: dict | None = None,
) -> tuple[dict | None, list[str]]:
    """Merge a reviewer revision into the original, guarded by fabrication audit.

    Returns ``(merged, hard)`` when no hard fabrications, else ``(None, hard)``
    — the first pass stays intact.
    """
    kwargs = dict(merge_kwargs or {})
    merge_low_value = kwargs.pop("merge_low_value", False)
    exclude_companies = kwargs.pop("exclude_companies", None)
    merged = merge_content(
        deepcopy(original), revision, jd_text, **kwargs, merge_low_value=merge_low_value
    )
    hard, _ = check_fabrication(
        original, merged, master_text, jd_text,
        merge_low_value=merge_low_value, exclude_companies=exclude_companies,
    )
    if hard:
        return None, hard
    return merged, hard


def bounded_revise(
    original: dict,
    draft: dict,
    master_text: str,
    jd_text: str,
    reviewer: Callable,
    verify_text: str = "",
    merge_kwargs: dict | None = None,
) -> tuple[dict, ReviewResult]:
    """Bounded single-revise loop: one review, at most one revision.

    ``draft`` is the full merged RenderCV dict from the first pass;
    ``reviewer(draft_text, master_text, jd_text, verify_text) -> ReviewResult``
    is injectable for tests. A rejected revision (fabrication guard) leaves
    the first pass intact. EXACTLY ONE revise pass — never loops.
    """
    review = reviewer(yaml.safe_dump(draft), master_text, jd_text, verify_text)
    if not review.changed or review.revision is None:
        return draft, review
    revised, _hard = apply_revision(
        original, review.revision, master_text, jd_text, merge_kwargs=merge_kwargs
    )
    if revised is None:
        return draft, review
    return revised, review
