"""ATS text-layer verification for a rendered cv_tailored.pdf.

Before a tailored PDF is sent anywhere it must survive a dumb text-layer
extraction: contact details as real literals (not icon glyphs), no mojibake
(U+FFFD / private-use glyphs), a sane reading order, a page-count budget,
and an honest keyword-coverage report against the JD.

All checks are pure functions over the extracted text so they can be tested
offline with pypdf-built fixtures (see tests/test_verify.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pypdf import PdfReader

from .merge import _term_matches, extract_jd_terms

# Curly apostrophes pypdf/font encodings may emit for a straight quote.
_APOSTROPHE_VARIANTS = {
    "\u2018",  # left single quotation mark
    "\u2019",  # right single quotation mark
    "\u201a",  # single low-9 quotation mark
    "\u201b",  # single high-reversed-9 quotation mark
    "\u2032",  # prime
}

# Replacement character + private-use area (missing-glyph / icon-font boxes).
_BAD_CODEPOINT_RE = re.compile("\ufffd|[\ue000-\uf8ff]")
# UTF-8 bytes of U+FFFD misread as Latin-1/WinAnsi (classic double-encoded
# replacement character, e.g. pypdf round-tripping a bad glyph).
_BAD_SEQUENCE_RE = re.compile("\u00ef\u00bf[\u0080-\u00bf\u2030-\u203a]?")

# Only these string cv fields feed check_contact_literal / reading-order anchors.
_CONTACT_FIELDS = {"name", "email", "phone", "location"}


@dataclass
class VerificationReport:
    """Aggregated result of verifying one rendered tailored PDF."""

    ok: bool
    contact_missing: list[str] = field(default_factory=list)
    mojibake: list[str] = field(default_factory=list)
    reading_order: list[str] = field(default_factory=list)
    page_count: tuple[int, int] = (0, 0)  # (observed, target)
    page_count_ok: bool = True
    keyword_coverage: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _normalize_for_match(text: str) -> str:
    """Normalize apostrophe variants and collapse whitespace for literal matching."""
    for ch in _APOSTROPHE_VARIANTS:
        text = text.replace(ch, "'")
    return re.sub(r"\s+", " ", text)


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract the full text layer of a PDF, joining pages with newlines."""
    reader = PdfReader(str(pdf_path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def check_contact_literal(text: str, contact: dict[str, str]) -> list[str]:
    """Return the contact fields whose value is not a literal in the text.

    Both sides are normalized (curly apostrophes -> straight, whitespace
    collapsed) so pypdf's apostrophe substitutions cannot hide a real
    contact value. Empty values are skipped.
    """
    norm_text = _normalize_for_match(text)
    missing: list[str] = []
    for label, value in contact.items():
        if not value:
            continue
        if _normalize_for_match(value) not in norm_text:
            missing.append(label)
    return missing


def check_mojibake(text: str) -> list[str]:
    """Return offending regions containing U+FFFD or private-use glyphs.

    Each report carries surrounding context so the offending region can be
    located; an empty list means the text layer is clean.
    """
    offending: list[str] = []
    for regex in (_BAD_CODEPOINT_RE, _BAD_SEQUENCE_RE):
        for m in regex.finditer(text):
            start, end = m.span()
            context = text[max(0, start - 20):end + 20].strip()
            report = f"mojibake: ...{context}..."
            if report not in offending:
                offending.append(report)
    return offending


def check_reading_order(text: str, anchors: list[str]) -> list[str]:
    """Check that anchors appear in order (first occurrences, case-insensitive).

    A missing anchor or a later anchor appearing before an earlier one is
    reported as a problem string.
    """
    lower = _normalize_for_match(text).lower()
    positions: list[int | None] = [lower.find(_normalize_for_match(a).lower()) for a in anchors]
    problems: list[str] = []
    last: int | None = None
    for anchor, pos in zip(anchors, positions):
        if pos is None or pos < 0:
            problems.append(f"reading order: anchor '{anchor}' not found in text layer")
            continue
        if last is not None and pos < last:
            problems.append(
                f"reading order: '{anchor}' appears before the previous anchor"
            )
        last = pos
    return problems


def check_page_count(pdf_path: Path, target: int) -> tuple[bool, int]:
    """Return (ok, observed) where ok means observed <= target."""
    observed = len(PdfReader(str(pdf_path)).pages)
    return observed <= target, observed


def keyword_coverage(text: str, jd_text: str, master_text: str) -> dict:
    """Bucket JD terms into covered / supported_missing / genuine_gap.

    covered: the term appears in the tailored text (word-boundary match).
    supported_missing: absent from the text but present in the master resume —
    the profile supports it, the drafter just missed it.
    genuine_gap: absent from both — an honesty-report gap, never stuffed.
    """
    jd_terms = extract_jd_terms(jd_text)
    if not jd_terms:
        return {
            "covered": [],
            "supported_missing": [],
            "genuine_gap": [],
            "no_signal": True,
        }
    covered: list[str] = []
    supported_missing: list[str] = []
    genuine_gap: list[str] = []
    for term in jd_terms:
        if _term_matches(text, term):
            covered.append(term)
        elif _term_matches(master_text, term):
            supported_missing.append(term)
        else:
            genuine_gap.append(term)
    return {
        "covered": covered,
        "supported_missing": supported_missing,
        "genuine_gap": genuine_gap,
    }
def verify_pdf(
    pdf_path: Path,
    master: dict,
    jd_text: str,
    target_pages: int,
    master_text: str | None = None,
) -> VerificationReport:
    """Run every ATS text-layer check on a rendered tailored PDF."""
    text = extract_pdf_text(pdf_path)

    if not text.strip():
        pages_ok, observed = check_page_count(pdf_path, target_pages)
        return VerificationReport(
            ok=False,
            page_count=(observed, target_pages),
            page_count_ok=pages_ok,
            keyword_coverage={"covered": [], "supported_missing": [], "genuine_gap": [], "no_signal": True},
            problems=["No extractable text layer found in PDF (vector-only render or font failure)."],
        )

    cv = master.get("cv", {}) or {}
    contact = {k: v for k, v in cv.items() if k in _CONTACT_FIELDS and isinstance(v, str)}
    contact_missing = check_contact_literal(text, contact)
    mojibake = check_mojibake(text)
    anchors = [contact.get("name", ""), contact.get("email", ""), "EXPERIENCE", "SKILLS"]
    reading_order = check_reading_order(text, anchors)
    page_count_ok, observed = check_page_count(pdf_path, target_pages)
    mt = master_text if master_text is not None else yaml.safe_dump(master)
    coverage = keyword_coverage(text, jd_text, mt)

    problems: list[str] = []
    if contact_missing:
        problems.append("Contact details missing from text layer: " + ", ".join(contact_missing))
    if mojibake:
        problems.append("Mojibake detected in text layer: " + " | ".join(mojibake[:3]))
    if reading_order:
        problems.extend(reading_order)
    if not page_count_ok:
        problems.append(
            f"Page count {observed} exceeds target of {target_pages} pages."
        )

    return VerificationReport(
        ok=not problems,
        contact_missing=contact_missing,
        mojibake=mojibake,
        reading_order=reading_order,
        page_count=(observed, target_pages),
        page_count_ok=page_count_ok,
        keyword_coverage=coverage,
        problems=problems,
    )
