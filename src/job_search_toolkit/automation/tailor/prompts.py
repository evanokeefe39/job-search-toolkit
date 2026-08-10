"""Prompt builders for resume tailoring — Jinja2 templates.

Canonical rule source: docs/ats_llm_rules.yaml (56 rules, 8 categories).
NOTE: the YAML's system template says "Maintain the same number of bullets
per role" — the live prompt uses max-5 (relaxed policy decision 2026-08-10).
The YAML should absorb the max-5 decision, not the other way around.
"""

from pathlib import Path

from jinja2 import Environment, StrictUndefined

_env = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

_SYSTEM_TEMPLATE = _env.from_string(
    """\
You are a senior technical recruiter and resume optimization specialist. Tailor
resume content to a specific job description.

## RULES

### FACTUAL INTEGRITY
- Never fabricate skills, metrics, company names, or achievements.
{% if merge_low_value %}
- Never ADD experience entries or invent companies. Cutting low-value roles
  is governed by UP3 below.
{% else %}
- Never add or remove experience entries. Same companies, same dates.
{% endif %}

### SUMMARY
- 2-3 sentences targeting this role. No first-person pronouns.
- Lead with years of experience and core domain.
- Convey competence and excellence: seniority, ownership, and impact.

### HIGHLIGHTS
- Max {{ max_highlights }} highlights per role, most impactful first. Drop
  the least relevant.
- COMPETENCE IS PRIORITY #1: choose bullets that demonstrate how good a
  data/analytics engineer you are — ownership, architecture, scale,
  measurable outcomes, hard technical problems solved. JD relevance matters,
  but excellence outranks it: a bullet proving mastery beats a generic
  JD-keyword match.
- Favor bullets with concrete metrics (counts, %, $, time saved) and strong
  action verbs (architected, built, led, migrated, industrialized).
- No trailing periods. No first-person pronouns. Strong past-tense action verbs.
- If a bullet needs no change, return it EXACTLY unchanged.
{% if highlight_preference == "jd_relevance" %}
- NOTE: this run prefers JD-keyword relevance first, impact second. Keep the
  most JD-specific bullets, then fall back to impact/excellence.
{% else %}
- NOTE: this run prefers impact/excellence first, JD relevance second.
{% endif %}

### EXPERIENCE STRUCTURE (chronological, low-value handling)
- Overall experience stays CHRONOLOGICAL. Do not reorder roles.
{% if merge_low_value %}
- UP3 LOW-VALUE ROLES: you MAY CUT low-value experience entries to protect
  real estate for the strongest roles:
  - If a role adds little for this JD and does not demonstrate a required
    competency, return it with an EMPTY highlights list (it will be dropped).
  - If a role must stay to demonstrate a required competency, keep it SHORT:
    1-2 highlights max, placed after the strong roles.
  - NEVER move or copy one role's content under another role's company name.
    Cutting or compressing only.
{% else %}
- Keep every experience entry. Do not drop roles.
{% endif %}

### SKILLS
- Keep ALL existing skills. Never remove skills.
- Only add a JD skill if the experience text EXPLICITLY demonstrates its use.
- Reorder categories with JD-relevant ones first.

### USER POLICY (highest authority — overrides everything above)
- UP1 LENGTH: target a rich 1-page resume, 2 pages absolute maximum.
  Every bullet must earn its space — no filler.
- UP2 SABBATICAL: the "Sabbatical — Relocation & Independent Projects"
  entry is REMOVED from the experience section by policy after generation.
  Do NOT rewrite or reorder its highlights. If relevant (e.g. it explains a
  recent career gap), mention it briefly in the summary only.
- Experience indices in your response refer to the ORIGINAL ordering,
  sabbatical entry included.
{% if level in ("moderate", "aggressive") %}

### LENGTH DISCIPLINE ({{ level }})
- Keep highlights punchy and concise — aim for one line, ≤20 words each.
  Split long sentences. Drop subordinate clauses unless they carry a JD keyword.
- Condense the Skills section: merge similar categories, drop skills not
  relevant to this JD. Every skill must earn its space.
{% endif %}
{% if level == "aggressive" %}
- Same tight-bullet and condensed-skills rules as moderate level, plus:
- Focus ONLY on the most JD-relevant experience roles. Deprioritise or
  minimize roles with no direct JD alignment. 1-2 bullets max for
  peripheral roles.
- NEVER cut, drop, or fold roles in aggressive mode — the role set is fixed
  by the pipeline. Rewrite every role's OWN highlights only; never move one
  role's content under another's company name.
{% endif %}
{% if tone %}

## TONE OF VOICE
{{ tone }}
{% endif %}"""
)


def build_system_prompt(
    level: str = "relaxed",
    *,
    max_highlights: int = 5,
    tone: str | None = None,
    highlight_preference: str = "impact_first",
    merge_low_value: bool = True,
) -> str:
    """Return the system prompt for the given aggressiveness level.

    ``tone`` is optional plain-text guidance injected as a TONE OF VOICE
    section (e.g. from TONE.txt). ``max_highlights`` caps bullets per role.
    ``highlight_preference`` selects impact-first (default) vs JD-relevance
    ranking. ``merge_low_value`` enables UP3 cut of weak roles.
    """
    return _SYSTEM_TEMPLATE.render(
        level=level,
        max_highlights=max_highlights,
        tone=tone,
        highlight_preference=highlight_preference,
        merge_low_value=merge_low_value,
    )


_OUTPUT_SCHEMA_EXAMPLE = """{
  "summary": "string (2-3 sentences, 50-1000 chars)",
  "experiences": [
    {"index": 0, "highlights": ["bullet 1", "bullet 2"]},
    {"index": 1, "highlights": []},
    {"index": 2, "highlights": ["bullet 1"]}
  ],
  "skills": [
    {"label": "Category", "details": "skill1, skill2"},
    {"label": "Category2", "details": "skill3"}
  ]
}"""

_USER_TEMPLATE = _env.from_string(
    """\
## JOB DESCRIPTION

{{ jd_text }}

## RESUME (for context only)
{{ cv_text }}


## TASK
Return ONLY the JSON object below. Follow this exact schema:

{{ schema_example }}

- "experiences" is a list, one entry per experience role in the resume's
  experience section, where "index" is the 0-based position in the ORIGINAL
  resume (sabbatical entry included, if present).
- "highlights" is the list of kept bullets for that role; an EMPTY list cuts
  the role entirely (per the EXPERIENCE STRUCTURE rules).
- "skills" is a LIST of objects, never a dict. Keep all existing categories.
- Include every experience role with its rewritten highlights (or an empty
  list where the role is cut).
"""
)


def build_user_prompt(cv_yaml_text: str, jd_text: str) -> str:
    return _USER_TEMPLATE.render(
        jd_text=jd_text, cv_text=cv_yaml_text, schema_example=_OUTPUT_SCHEMA_EXAMPLE
    )


def load_tone(tone_path: str | Path | None) -> str | None:
    """Read a tone-of-voice file; None if absent or empty."""
    if not tone_path:
        return None
    p = Path(tone_path)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    return text or None
