"""Deterministic fabrication audit for tailored resumes."""

import json
import re


def _skill_tokens(section: list[dict]) -> set[str]:
    tokens: set[str] = set()
    for entry in section:
        for token in re.split(r"[,;]+", entry.get("details", "")):
            if t := token.strip().lower():
                tokens.add(t)
    return tokens


_METRIC_RE = re.compile("|".join([
    r"\bby\s+\d[\d,]*%", r"\bby\s+\$\d[\d,]*", r"\bby\s+\d[\d,]*x\b",
    r"\$\d[\d,]*[KMB]?\b",
    r"\d[\d,]*%\s*(?:of|reduction|improvement|increase|decrease|savings|cut|faster|less)",
    r"\d[\d,]*x\s*(?:faster|improvement|reduction)",
    r"\bby\s+\d[\d,]*\s*(?:hours|days|weeks|months|minutes|seconds)",
    r"reduc(?:ed|ing)\s+\w+\s+by\s+\d+", r"cut\s+\w+\s+by\s+\d+",
    r"sav(?:ed|ing)\s+\$\d+", r"\bfrom\s+\d[\d,]*\s+to\s+\d[\d,]*\b",
]), re.IGNORECASE)

SYNONYMS = {"k8s": "kubernetes", "gh": "github", "ci": "ci/cd", "cd": "ci/cd"}


def check_fabrication(original: dict, tailored: dict, orig_text: str, jd_text: str):
    hard, jd_adds = [], []
    ol, jl = orig_text.lower(), jd_text.lower()
    # UP2: sabbatical removal is policy, not fabrication — exclude sabbatical
    # entries from the count/company comparison on both sides.
    def _no_sab(entries: list[dict]) -> list[dict]:
        return [e for e in entries
                if "sabbatical" not in str(e.get("company", "")).lower()]
    oe = _no_sab(original.get("cv", {}).get("sections", {}).get("experience", []))
    ne = _no_sab(tailored.get("cv", {}).get("sections", {}).get("experience", []))
    if len(ne) != len(oe):
        hard.append(f"Exp count: {len(oe)} -> {len(ne)}")
    for i, (a, b) in enumerate(zip(oe, ne)):
        if a.get("company") != b.get("company"):
            hard.append(f"Company: '{a.get('company')}' -> '{b.get('company')}'")
    os_ = _skill_tokens(original.get("cv", {}).get("sections", {}).get("skills", []))
    ns_ = _skill_tokens(tailored.get("cv", {}).get("sections", {}).get("skills", []))
    for s in ns_ - os_:
        if s in SYNONYMS or any(SYNONYMS.get(s) == o for o in os_):
            continue
        if s in ol:
            continue
        (jd_adds if s in jl else hard).append(s)
    seen: set[str] = set()
    for m in _METRIC_RE.finditer(json.dumps(tailored)):
        for n in re.findall(r"\d[\d,]*", m.group()):
            if n not in seen and n not in orig_text:
                hard.append(f"Fabricated metric: {n}")
                seen.add(n)
    return hard, jd_adds
