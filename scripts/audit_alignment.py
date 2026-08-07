"""Deterministic alignment audit for tailored resumes.

Salvaged from the deleted ATS orchestrator (src/ats_pipeline/run.py):
strip_fabricated_content deterministically removes claims the original
resume can't support (unverifiable skills, fabricated metrics).

Usage: uv run python scripts/audit_alignment.py <original_file> <rewrite_file>
Exit code: 0 if nothing was stripped, 1 if anything was stripped.
"""

import re

# ── Deterministic Alignment Check (mirrors Resume-Matcher Pass 3) ───

_METRIC_PATTERNS = [
    r"[\d,]+\.?\d*\s*%",
    r"by\s+[\d,]+\.?\d*\s*%?",
    r"\$\s?[\d,]+\.?\d*",
    r"[\d,]+\s*x",
    r"[\d,]+\.?\d*\s*(?:million|billion|thousand)\b",
    r"(?:over|more than|about|approx|roughly)\s+[\d,]+\s*[kKmMbB]?\b",
    r"[\d,]+\s*(?:records?|reports?|users?|customers?|clients?|systems?|"
    r"datasets?|sources?|requests?|transactions?|rows?|tables?|pipelines?|"
    r"models?|developers?|engineers?|dashboards?|team members?|data errors?)",
]
_METRIC_RE = re.compile("|".join(_METRIC_PATTERNS), re.IGNORECASE)

# Cleanup after metric removal: collapse double spaces, drop dangling "by "
_CLEANUP = re.compile(r"\s{2,}|\bby\s+(?=[.,;:]|$)", re.IGNORECASE)


def _extract_section(text: str, header: str) -> list[str]:
    """Return the lines under a Markdown ## header."""
    lines = text.split("\n")
    in_section = False
    out: list[str] = []
    for line in lines:
        if line.startswith("## "):
            in_section = line[3:].strip().lower() == header
            continue
        if in_section and line.strip():
            out.append(line)
    return out


def _normalize_skill_token(part: str) -> str:
    """Normalize a raw skill fragment to a comparable token.

    Strips markdown bold, bullets, label prefixes, parenthetical qualifiers
    (e.g. "JavaScript (basic)" -> "javascript"), and trailing punctuation
    (e.g. "Databricks)" -> "databricks").
    """
    part = part.replace("**", "").strip()       # drop markdown bold markers
    part = re.sub(r"^[*\-\s]+", "", part.strip())
    part = re.split(r":\s*", part)[-1].strip()
    part = re.sub(r"\s*\(.*?\)\s*$", "", part)  # trailing (qualifier)
    part = re.sub(r"\(.*?\)", "", part)         # inline (expansions)
    part = re.sub(r"[\s\-_]+", " ", part).strip()
    part = re.sub(r"[^a-z0-9+#.]+$", "", part.lower())
    return part


def _extract_skills(text: str) -> set[str]:
    """Flatten the Skills section into normalized skill tokens."""
    skills: set[str] = set()
    for line in _extract_section(text, "skills"):
        parts = re.split(r"[,|•\n]", line)
        for part in parts:
            token = _normalize_skill_token(part)
            if token and len(token) > 1:
                skills.add(token)
    return skills


def _original_numbers(text: str) -> set[str]:
    """All numeric tokens in the original (dates, phone, years — the legit ones)."""
    return set(re.findall(r"\d[\d,]*", text))


def strip_fabricated_content(original: str, rewrite: str) -> tuple[str, list[str]]:
    """Deterministically remove claims the original resume can't support.

    Returns (cleaned_rewrite, decisions) where each decision is a
    human-readable log line explaining what was stripped and why.
    """
    decisions: list[str] = []

    # 1. Skills: remove any skill in the rewrite not verifiable anywhere in the original
    orig_skills = _extract_skills(original)
    rewrite_skills = _extract_skills(rewrite)
    orig_lower = original.lower()
    added = set()
    for s in rewrite_skills:
        if s in orig_skills:
            continue  # in the Skills section — verifiable
        if s in orig_lower:
            continue  # mentioned in a bullet/project — still verifiable
        added.add(s)

    if added:
        new_lines: list[str] = []
        for line in rewrite.split("\n"):
            if line.startswith("## "):
                new_lines.append(line)
                continue
            if line.strip():
                line_lower = line.lower()
                removed_here = [s for s in added if s in line_lower]
                if removed_here:
                    line_skills = [s for s in _extract_skills(line) if s]
                    if line_skills and all(s in added for s in line_skills):
                        decisions.append(
                            f"skills: dropped '{line.strip()}' (not in original: {', '.join(removed_here)})"
                        )
                        continue
                    cleaned = line
                    for s in removed_here:
                        cleaned = re.sub(rf"\s*,\s*{re.escape(s)}\b", "", cleaned, flags=re.IGNORECASE)
                        cleaned = re.sub(rf"^\s*{re.escape(s)}\b\s*[,:]?\s*", "", cleaned, flags=re.IGNORECASE)
                        cleaned = re.sub(rf"\s*[-–]\s*{re.escape(s)}\b", "", cleaned, flags=re.IGNORECASE)
                    decisions.append(
                        f"skills: removed {', '.join(removed_here)} from '{line.strip()}'"
                    )
                    line = cleaned
                if line.strip():
                    new_lines.append(line)
            else:
                new_lines.append(line)
        rewrite = "\n".join(new_lines)

    # 2. Metrics: strip quantified claims whose numbers aren't in the original
    legit_numbers = _original_numbers(original)
    new_lines = []
    for line in rewrite.split("\n"):
        if line.startswith("## ") or not line.strip():
            new_lines.append(line)
            continue
        for match in _METRIC_RE.findall(line):
            nums = re.findall(r"\d[\d,]*", str(match))
            if nums and all(n not in legit_numbers for n in nums):
                decisions.append(
                    f"metrics: stripped '{match}' from: {line.strip()[:80]}"
                )
                line = line.replace(str(match), "").strip()
        # Tidy grammar artifacts left by stripping ("reducing deployment time by",
        # double spaces)
        if _METRIC_RE.search(line) is None:
            line = _CLEANUP.sub(" ", line).strip()
        new_lines.append(line)
    rewrite = "\n".join(new_lines)

    return rewrite, decisions


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 3:
        print("Usage: uv run python scripts/audit_alignment.py <original_file> <rewrite_file>")
        sys.exit(2)

    original = Path(sys.argv[1]).read_text(encoding="utf-8")
    rewrite = Path(sys.argv[2]).read_text(encoding="utf-8")

    cleaned, decisions = strip_fabricated_content(original, rewrite)

    if decisions:
        for d in decisions:
            print(d)
    else:
        print("No fabricated content detected — nothing stripped.")

    print(
        f"Summary: {len(decisions)} strip decision(s); "
        f"rewrite {len(rewrite.split())} -> {len(cleaned.split())} words"
    )
    sys.exit(1 if decisions else 0)
