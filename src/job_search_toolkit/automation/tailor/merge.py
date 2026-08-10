"""YAML I/O, merge, and impact/JD-relevance-aware highlight trimming."""

import difflib
import re
import sys
from copy import deepcopy
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_text(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


_JD_SECTION_RE = re.compile(r"^## (Technologies|Competencies)\s*$", re.IGNORECASE)


def extract_jd_terms(jd_text: str) -> list[str]:
    """Parse the JD's ``## Technologies`` / ``## Competencies`` lists into terms."""
    terms: list[str] = []
    in_list = False
    for line in jd_text.splitlines():
        s = line.strip()
        if s.startswith("## "):
            in_list = bool(_JD_SECTION_RE.match(s))
            continue
        if in_list and s.startswith("- "):
            term = s[2:].strip()
            if term and term.lower() != "n/a":
                terms.append(term)
    return terms


def _term_matches(bullet: str, term: str) -> bool:
    """Word-boundary match of a JD term inside a bullet.

    Multi-word terms (e.g. "Apache Airflow") also match on any single word
    of length >= 3, so a bullet saying just "Airflow" still counts.
    Single-char terms are ignored: "SQL" must never match "SQLite", and a
    lone "M" is noise.
    """
    if len(term) < 2:
        return False
    if re.search(r"\b" + re.escape(term) + r"\b", bullet, re.IGNORECASE):
        return True
    if " " in term:
        words = [w for w in term.split() if len(w) >= 3]
        return any(
            re.search(r"\b" + re.escape(w) + r"\b", bullet, re.IGNORECASE)
            for w in words
        )
    return False


def _jd_relevance(bullet: str, jd_terms: list[str]) -> float:
    """Fraction of JD terms matched by the bullet (0..1)."""
    if not jd_terms:
        return 0.0
    hits = sum(1 for t in jd_terms if _term_matches(bullet, t))
    return hits / len(jd_terms)


_METRIC_RE = re.compile(
    r"\b\d[\d,]*%|\$\d[\d,]*|\b\d[\d,]*x\b|\b\d[\d,]*\b(?:sources|systems|"
    r"services|domains|organisations|users|rows|records|people|members|"
    r"companies|subsidiaries|teams|projects|models|pipelines|dashboards)\b",
    re.IGNORECASE,
)

_STRONG_VERBS = {
    "architected", "automated", "built", "championed", "co-designed",
    "delivered", "designed", "drove", "established", "implemented",
    "industrialised", "industrialized", "led", "migrated", "modernised",
    "modernized", "orchestrated", "owned", "prevented", "reduced",
    "replaced", "scaled", "secured", "streamlined", "unified",
}

_SCALE_WORDS = {
    "greenfield", "enterprise", "enterprise-grade", "organisation-wide",
    "organization-wide", "cross-organisational", "cross-org", "multi-tenant",
    "production", "real-time", "platform", "100's", "hundreds", "thousands",
    "millions", "critical", "state-wide", "cloud-native", "custom-built",
}


def _impact_score(bullet: str) -> float:
    """Heuristic "how impressive is this bullet" (0..1, not a probability).

    Ranks bullets by the signals recruiters read as impact: concrete metrics
    (counts, %, $, x), strong past-tense action verbs, and scale words.
    User policy: competence/excellence outranks JD relevance when trimming,
    so this score is the primary sort key (see _trim_highlights).
    """
    score = 0.0
    metrics = len(_METRIC_RE.findall(bullet))
    if metrics:
        score += min(metrics, 3) * 0.30  # up to 0.90 for metric-rich bullets
    words = set(re.findall(r"[a-z]+", bullet.lower()))
    if words & _STRONG_VERBS:
        score += 0.25
    if words & _SCALE_WORDS:
        score += 0.20
    return min(score, 1.0)


def _trim_highlights(original_highlights: list[str], new_highlights: list[str],
                     jd_terms: list[str] | None = None, max_count: int = 5,
                     preference: str = "impact_first") -> list[str]:
    """Keep up to max_count highlights.

    Eligibility: a bullet must have some master analogue (overlap > 0) —
    the anti-fabrication guard; invented bullets drop first. Among eligible
    bullets, ranking depends on ``preference``:
      - "impact_first" (default): impact score desc, then JD relevance,
        then master-overlap tiebreak.
      - "jd_relevance": JD relevance desc, then impact, then master-overlap.
    Without JD terms, JD relevance is 0 for all — ranking degrades to
    impact-first (or master-overlap for jd_relevance preference).
    """
    if len(new_highlights) <= max_count:
        return new_highlights
    scored: list[tuple[float, float, float, str]] = []
    for nh in new_highlights:
        best = max(
            (difflib.SequenceMatcher(None, nh, oh).ratio() for oh in original_highlights),
            default=0.0,
        )
        impact = _impact_score(nh)
        jd_rel = _jd_relevance(nh, jd_terms or [])
        scored.append((best, impact, jd_rel, nh))
    if preference == "jd_relevance":
        # eligibility gate, then JD relevance, then impact, then master-overlap
        scored.sort(key=lambda x: (x[0] > 0, x[2], x[1], x[0]), reverse=True)
    else:
        # eligibility gate, then impact (user priority #1), then JD, then overlap
        scored.sort(key=lambda x: (x[0] > 0, x[1], x[2], x[0]), reverse=True)
    kept = [hl for _, _, _, hl in scored[:max_count]]
    dropped_bullets = [hl for _, _, _, hl in scored[max_count:]]
    print(f"[TRIM] {len(dropped_bullets)}/{len(new_highlights)} bullets dropped "
          f"({preference}, kept top {max_count})", file=sys.stderr)
    for i, db in enumerate(dropped_bullets):
        ov, imp, jd_rel, _ = scored[max_count + i]
        print(f"[TRIM]   dropped[{i}] (master_ov={ov:.2f}, impact={imp:.2f}, "
              f"jd_rel={jd_rel:.2f}): "
              f"{db[:110]}{'...' if len(db) > 110 else ''}", file=sys.stderr)
    return kept


def merge_content(original: dict, content: dict, jd_text: str | None = None,
                  preference: str = "impact_first",
                  merge_low_value: bool = False,
                  max_highlights: int = 5) -> dict:
    """Deep-copy original, then slot LLM content into the copy.

    ``jd_text`` feeds JD-term relevance into highlight trimming.
    ``preference`` selects the trim ranking ("impact_first" | "jd_relevance").
    ``merge_low_value`` enables UP3 low-value handling: roles whose LLM
    highlights list is empty are CUT from the output. When disabled (e.g.
    aggressive mode's deterministic role set), an empty list means "no
    change" — the role's master highlights are restored, never dropped or
    left blank (prevents content displacement under another header).
    ``max_highlights`` caps bullets kept per role.
    """
    result = deepcopy(original)
    sections = result["cv"]["sections"]
    jd_terms = extract_jd_terms(jd_text) if jd_text else None
    if "summary" in content:
        sections["summary"] = [content["summary"]]
    if "experiences" in content:
        orig_exp = sections.get("experience", [])
        for entry in content["experiences"]:
            idx = entry.get("index", 0)
            if idx >= len(orig_exp):
                continue
            if "highlights" in entry:
                new_hl = entry["highlights"]
                orig_hl = orig_exp[idx].get("highlights", [])
                if not new_hl and not merge_low_value:
                    # Deterministic role set (aggressive) or merge disabled:
                    # empty list = "no change" — restore master highlights.
                    continue
                orig_exp[idx]["highlights"] = _trim_highlights(
                    orig_hl, new_hl, jd_terms,
                    max_count=max_highlights, preference=preference,
                )
        if merge_low_value:
            # UP3: drop roles with no highlights (cut entirely)
            sections["experience"] = [
                e for e in orig_exp if e.get("highlights")
            ]
    if "skills" in content:
        sections["skills"] = content["skills"]
    return result


def emit_yaml(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    print(f"[INFO] Wrote: {path}", file=sys.stderr)


def validate_structure(data: dict, original: dict) -> None:
    sections = data.get("cv", {}).get("sections", {})
    oe = original.get("cv", {}).get("sections", {}).get("experience", [])
    ne = sections.get("experience", [])
    if len(ne) != len(oe):
        print(f"[WARN] Exp count: {len(oe)} -> {len(ne)}", file=sys.stderr)
