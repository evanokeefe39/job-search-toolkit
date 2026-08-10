"""Resume tailoring pipeline — models, prompts, merge, audit, render."""

from pipeline.tailor.models import HighlightsEntry, SkillEntry, TailorResponse
from pipeline.tailor.prompts import build_system_prompt, build_user_prompt
from pipeline.tailor.client import call_llm
from pipeline.tailor.merge import load_yaml, load_text, merge_content, emit_yaml, validate_structure
from pipeline.tailor.audit import check_fabrication
from pipeline.tailor.render import render_pdf

__all__ = [
    "HighlightsEntry", "SkillEntry", "TailorResponse",
    "build_system_prompt", "build_user_prompt",
    "call_llm",
    "load_yaml", "load_text", "merge_content", "emit_yaml", "validate_structure",
    "check_fabrication",
    "render_pdf",
]
