"""Resume tailoring pipeline — models, prompts, merge, audit, render."""

from job_search_toolkit.automation.tailor.models import HighlightsEntry, SkillEntry, TailorResponse
from job_search_toolkit.automation.tailor.prompts import build_system_prompt, build_user_prompt, load_tone
from job_search_toolkit.automation.tailor.client import call_llm
from job_search_toolkit.automation.tailor.config import (
    load_config,
    TONE_NONE,
    DEFAULT_CONFIG_PATH,
    DEFAULT_TAILOR_PREFERENCES_PATH,
)
from job_search_toolkit.automation.tailor.merge import load_yaml, load_text, merge_content, emit_yaml, validate_structure
from job_search_toolkit.automation.tailor.audit import check_fabrication
from job_search_toolkit.automation.tailor.render import render_pdf

__all__ = [
    "HighlightsEntry", "SkillEntry", "TailorResponse",
    "build_system_prompt", "build_user_prompt", "load_tone",
    "call_llm",
    "load_config", "TONE_NONE", "DEFAULT_CONFIG_PATH", "DEFAULT_TAILOR_PREFERENCES_PATH",
    "load_yaml", "load_text", "merge_content", "emit_yaml", "validate_structure",
    "check_fabrication",
    "render_pdf",
]
