"""Resume tailoring pipeline — models, prompts, merge, audit, render."""

from pipeline.tailor.models import HighlightsEntry, SkillEntry, TailorResponse
from pipeline.tailor.prompts import build_system_prompt, build_user_prompt, load_tone
from pipeline.tailor.client import call_llm
from pipeline.tailor.config import load_config, TONE_NONE, DEFAULT_CONFIG_PATH
from pipeline.tailor.merge import load_yaml, load_text, merge_content, emit_yaml, validate_structure
from pipeline.tailor.audit import check_fabrication
from pipeline.tailor.render import render_pdf

__all__ = [
    "HighlightsEntry", "SkillEntry", "TailorResponse",
    "build_system_prompt", "build_user_prompt", "load_tone",
    "call_llm",
    "load_config", "TONE_NONE", "DEFAULT_CONFIG_PATH",
    "load_yaml", "load_text", "merge_content", "emit_yaml", "validate_structure",
    "check_fabrication",
    "render_pdf",
]
