"""Resume tailoring pipeline — models, prompts, merge, audit, render, verify, review."""

from job_search_toolkit.automation.tailor.models import HighlightsEntry, SkillEntry, TailorResponse
from job_search_toolkit.automation.tailor.prompts import build_system_prompt, build_user_prompt, load_tone
from job_search_toolkit.automation.tailor.client import call_llm
from job_search_toolkit.automation.tailor.config import (
    load_config,
    TONE_NONE,
    DEFAULT_TAILOR_PREFERENCES_PATH,
)
from job_search_toolkit.automation.tailor.merge import load_yaml, load_text, merge_content, emit_yaml, validate_structure
from job_search_toolkit.automation.tailor.audit import check_fabrication
from job_search_toolkit.automation.tailor.render import render_pdf
from job_search_toolkit.automation.tailor.verify import (
    VerificationReport,
    extract_pdf_text,
    check_contact_literal,
    check_mojibake,
    check_reading_order,
    check_page_count,
    keyword_coverage,
    verify_pdf,
)
from job_search_toolkit.automation.tailor.reviewer import (
    ReviewResult,
    review_draft,
    apply_revision,
    bounded_revise,
)

__all__ = [
    "HighlightsEntry", "SkillEntry", "TailorResponse",
    "build_system_prompt", "build_user_prompt", "load_tone",
    "call_llm",
    "load_config", "TONE_NONE", "DEFAULT_TAILOR_PREFERENCES_PATH",
    "load_yaml", "load_text", "merge_content", "emit_yaml", "validate_structure",
    "check_fabrication",
    "render_pdf",
    "VerificationReport", "extract_pdf_text", "check_contact_literal",
    "check_mojibake", "check_reading_order", "check_page_count",
    "keyword_coverage", "verify_pdf",
    "ReviewResult", "review_draft", "apply_revision", "bounded_revise",
]
