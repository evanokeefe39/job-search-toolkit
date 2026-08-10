"""Prompt constants and builders for resume tailoring.

Canonical rule source: docs/ats_llm_rules.yaml (56 rules, 8 categories).
NOTE: the YAML's system template says "Maintain the same number of bullets
per role" — the live prompt uses max-5 (relaxed policy decision 2026-08-10).
The YAML should absorb the max-5 decision, not the other way around.
"""

import textwrap

_BASE_PROMPT = textwrap.dedent("""\
You are a senior technical recruiter and resume optimization specialist. Tailor
resume content to a specific job description.

## RULES

### FACTUAL INTEGRITY
- Never fabricate skills, metrics, company names, or achievements.
- Never add or remove experience entries. Same companies, same dates.

### SUMMARY
- 2-3 sentences targeting this role. No first-person pronouns.
- Lead with years of experience and core domain.

### HIGHLIGHTS
- Max 5 highlights per role, most JD-relevant first. Drop the least relevant.
- No trailing periods. No first-person pronouns. Strong past-tense action verbs.
- If a bullet needs no change, return it EXACTLY unchanged.

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
""")

_MODERATE_ADDITIONS = textwrap.dedent("""\

### LENGTH DISCIPLINE (moderate)
- Keep highlights punchy and concise — aim for one line, ≤20 words each.
  Split long sentences. Drop subordinate clauses unless they carry a JD keyword.
- Condense the Skills section: merge similar categories, drop skills not
  relevant to this JD. Every skill must earn its space.
""")

_AGGRESSIVE_ADDITIONS = textwrap.dedent("""\

### LENGTH DISCIPLINE (aggressive — 1-page target)
- Same tight-bullet and condensed-skills rules as moderate level, plus:
- Focus ONLY on the most JD-relevant experience roles. Deprioritise or
  minimize roles with no direct JD alignment. 1-2 bullets max for
  peripheral roles.
""")


def build_system_prompt(level: str = "relaxed") -> str:
    """Return the system prompt for the given aggressiveness level."""
    prompt = _BASE_PROMPT
    if level in ("moderate", "aggressive"):
        prompt += _MODERATE_ADDITIONS
    if level == "aggressive":
        prompt += _AGGRESSIVE_ADDITIONS
    return prompt


_OUTPUT_SCHEMA_EXAMPLE = """{
  "summary": "string (2-3 sentences, 50-1000 chars)",
  "experiences": [
    {"index": 0, "highlights": ["bullet 1", "bullet 2"]},
    {"index": 1, "highlights": ["bullet 1"]}
  ],
  "skills": [
    {"label": "Category", "details": "skill1, skill2"},
    {"label": "Category2", "details": "skill3"}
  ]
}"""


def build_user_prompt(cv_yaml_text: str, jd_text: str) -> str:
    return textwrap.dedent(f"""\
## JOB DESCRIPTION

{jd_text}

## RESUME (for context only)
{cv_yaml_text}


## TASK
Return ONLY the JSON object below. Follow this exact schema:

{_OUTPUT_SCHEMA_EXAMPLE}

- "experiences" is a list, one entry per experience role in the resume's
  experience section, where "index" is the 0-based position in the ORIGINAL
  resume (sabbatical entry included, if present).
- "skills" is a LIST of objects, never a dict. Keep all existing categories.
- Include every experience role with its rewritten highlights.
""")
