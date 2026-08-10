"""Deterministic fabrication audit for tailored resumes."""

import difflib
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

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "were", "was",
    "have", "has", "had", "been", "being", "are", "not", "but", "you",
    "your", "our", "their", "they", "them", "into", "across", "over",
    "under", "through", "during", "while", "when", "where", "which",
    "what", "who", "whom", "whose", "about", "than", "then", "there",
    "here", "will", "would", "could", "should", "can", "may", "might",
}


def _words(text: str) -> set[str]:
    """Significant words in a string (len>=3, lowercase, no stopwords)."""
    return {w for w in re.findall(r"[a-z][a-z0-9+#.-]*", text.lower())
            if len(w) >= 3 and w not in _STOPWORDS}


def _word_similar(a: str, b: str) -> bool:
    """Fuzzy single-word match tolerant of inflections (models/integrating)."""
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _jd_term_reworded(skill: str, jd_text: str) -> bool:
    """True if skill is a reworded JD term (word overlap, order-insensitive).

    Catches the LLM flipping JD competencies like "monitoring pipelines"
    into skill entries like "pipeline monitoring" — same words, different
    order, possibly inflected ("integrating models" -> "model integration").
    Exact-substring checks would misclassify those as fabrications.
    """
    sw = _words(skill)
    if not sw:
        return False
    jw = _words(jd_text)
    matched = 0
    for sw_word in sw:
        if any(_word_similar(sw_word, jw_word) for jw_word in jw):
            matched += 1
    if len(sw) >= 2:
        return matched / len(sw) >= 0.5
    return matched >= 1


def _no_sab(entries: list[dict]) -> list[dict]:
    """UP2: sabbatical removal is policy, not fabrication — exclude those."""
    return [e for e in entries
            if "sabbatical" not in str(e.get("company", "")).lower()]


def check_fabrication(original: dict, tailored: dict, orig_text: str, jd_text: str,
                      merge_low_value: bool = False,
                      exclude_companies: set[str] | None = None) -> tuple[list, list]:
    """Detect fabrication in a tailored resume.

    ``merge_low_value`` allows UP3 cuts: counts may differ, but every company
    still present must exist in the master. ``exclude_companies`` (lowercase
    substrings, e.g. the aggressive mode's non-kept roles) marks deterministic
    role cuts that are policy, not fabrication — mirroring UP2's sabbatical
    exclusion in ``_no_sab``. Both sides are filtered identically so a
    policy-driven cut is never a false HARD.
    """
    hard, jd_adds = [], []
    ol, jl = orig_text.lower(), jd_text.lower()

    def _filter(entries: list[dict]) -> list[dict]:
        out = _no_sab(entries)
        if exclude_companies:
            out = [e for e in out
                   if not any(c in str(e.get("company", "")).lower()
                              for c in exclude_companies)]
        return out

    oe = _filter(original.get("cv", {}).get("sections", {}).get("experience", []))
    ne = _filter(tailored.get("cv", {}).get("sections", {}).get("experience", []))
    oe_names = {str(e.get("company", "")).lower() for e in oe}
    if merge_low_value:
        # UP3: low-value roles may be cut — but every company still present
        # must exist in the original (no invented employers), whether or not
        # the counts happen to match.
        for b in ne:
            if str(b.get("company", "")).lower() not in oe_names:
                hard.append(f"Company: '{b.get('company')}' not in master")
    else:
        if len(ne) != len(oe):
            hard.append(f"Exp count: {len(oe)} -> {len(ne)}")
        for a, b in zip(oe, ne):
            if a.get("company") != b.get("company"):
                hard.append(f"Company: '{a.get('company')}' -> '{b.get('company')}'")
    os_ = _skill_tokens(original.get("cv", {}).get("sections", {}).get("skills", []))
    ns_ = _skill_tokens(tailored.get("cv", {}).get("sections", {}).get("skills", []))
    for s in ns_ - os_:
        if s in SYNONYMS or any(SYNONYMS.get(s) == o for o in os_):
            continue
        if s in ol:
            continue
        if s in jl or _jd_term_reworded(s, jd_text):
            jd_adds.append(s)
        else:
            hard.append(s)
    seen: set[str] = set()
    for m in _METRIC_RE.finditer(json.dumps(tailored)):
        for n in re.findall(r"\d[\d,]*", m.group()):
            if n not in seen and n not in orig_text:
                hard.append(f"Fabricated metric: {n}")
                seen.add(n)
    return hard, jd_adds
