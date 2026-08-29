"""Contract tests for WS3 Epic 3.1 — ATS text-layer verification.

Deterministic and offline: fixture PDFs are built in-test with pypdf content
streams (no RenderCV, no network). These tests pin the public API of
``job_search_toolkit.automation.tailor.verify`` before any implementation.

Validation Tests (tasks/plans/ws3-tailoring-quality.md):
- test_ats_check_contact_literal
- test_ats_check_mojibake
- test_ats_check_reading_order
- test_ats_check_page_count
- test_keyword_coverage_buckets
+ edge cases: no extractable text layer; JD with no keywords (not a failing 0%).
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
	DecodedStreamObject,
	DictionaryObject,
	NameObject,
)

from job_search_toolkit.automation.tailor.verify import (  # noqa: E402
    check_contact_literal,
    check_mojibake,
    check_page_count,
    check_reading_order,
    extract_pdf_text,
    keyword_coverage,
    verify_pdf,
)


# ---------------------------------------------------------------------------
# Deterministic text-PDF fixture builder (pypdf content streams, no network).
# ---------------------------------------------------------------------------
def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path: Path, pages_text: list[str], page_size: tuple[int, int] = (612, 792)) -> Path:
    """Write a PDF whose pages each carry ``pages_text[i]`` as Helvetica text."""
    w, h = page_size
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    for txt in pages_text:
        page = writer.add_blank_page(width=w, height=h)
        lines = "\n".join(
            f"{i * 20} 0 Td ({_esc(ln)}) Tj 0 -20 Td"
            for i, ln in enumerate(txt.split("\n"))
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 11 Tf 72 {h - 72} Td {lines} ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = resources
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


CONTACT = {
    "name": "Ada Lovelace",
    "email": "ada.lovelace@example.com",
    "phone": "+1 555 010 0199",
    "location": "London, UK",
}

JD_TEXT = """## Technologies
- Python
- Apache Airflow
- PySpark
## Competencies
- Data Engineering
"""

MASTER_TEXT = "Python\nSQL\nData Engineering\nGCP\nBigQuery"


def test_ats_check_contact_literal(tmp_path):
    """Email/phone/location as real text passes; an icon-glyph email fails."""
    good = make_pdf(tmp_path / "good.pdf", [
        "Ada Lovelace\nLondon, UK\nada.lovelace@example.com\n+1 555 010 0199\n\nEXPERIENCE\n",
    ])
    text = extract_pdf_text(good)
    # pypdf may render the straight apostrophe as a curly one — contact
    # matching must normalize so a real ATS-style literal match still passes.
    assert text
    assert check_contact_literal(text, CONTACT) == []

    # Icon-glyph email: the glyph box is not the literal address.
    bad = make_pdf(tmp_path / "bad.pdf", [
        "Ada Lovelace\nLondon, UK\n[\u25a1 glyph]\n+1 555 010 0199\n",
    ])
    missing = check_contact_literal(extract_pdf_text(bad), CONTACT)
    assert any("email" in m for m in missing)


def test_ats_check_mojibake():
    """U+FFFD / missing-glyph codepoints are reported with the offending region."""
    assert check_mojibake("Clean text with apostrophes and \u2014 em-dashes.") == []
    offending = check_mojibake("Built pipelines \ufffd in \uf0b7 Python and PySpark.")
    assert offending, "mojibake must not pass silently"
    joined = " | ".join(offending).lower()
    assert "\ufffd" in joined or "mojibake" in joined or "replacement" in joined


def test_ats_check_reading_order():
    """name -> contact -> experience -> skills must hold; a reorder fails."""
    correct = (
        "Ada Lovelace\nLondon, UK\nada.lovelace@example.com\n"
        "EXPERIENCE\nBuilt pipelines\nSKILLS\nPython SQL"
    )
    assert check_reading_order(correct, [
        "Ada Lovelace", "ada.lovelace@example.com", "EXPERIENCE", "SKILLS",
    ]) == []

    reordered = (
        "SKILLS\nPython SQL\nEXPERIENCE\nBuilt pipelines\n"
        "Ada Lovelace\nada.lovelace@example.com"
    )
    problems = check_reading_order(reordered, [
        "Ada Lovelace", "ada.lovelace@example.com", "EXPERIENCE", "SKILLS",
    ])
    assert problems, "reordered sections must be flagged"


def test_ats_check_page_count(tmp_path):
    """2-page passes; 3-page fails; exactly at the limit passes."""
    two = make_pdf(tmp_path / "two.pdf", ["page one", "page two"])
    ok, observed = check_page_count(two, 2)
    assert ok is True and observed == 2

    three = make_pdf(tmp_path / "three.pdf", ["p1", "p2", "p3"])
    ok3, obs3 = check_page_count(three, 2)
    assert ok3 is False and obs3 == 3

    # off-by-one: exactly at the limit must pass, one over must fail.
    ok_again, _ = check_page_count(two, 2)
    assert ok_again is True


def test_keyword_coverage_buckets():
    """covered / supported-but-missing / genuine-gap, honesty rule (no stuffing)."""
    text = "Ada Lovelace\nPython\nEXPERIENCE\nBuilt pipelines with Apache Airflow"
    cov = keyword_coverage(text, JD_TEXT, MASTER_TEXT)
    assert cov["covered"] == ["Python", "Apache Airflow"]
    assert cov["supported_missing"] == ["Data Engineering"]  # in master, missed
    assert cov["genuine_gap"] == ["PySpark"]                 # JD-only, profile lacks
    # honesty: a genuine gap is reported as a gap, never stuffed into covered.
    assert "PySpark" not in cov["covered"]


def test_keyword_coverage_no_jd_signal():
    """A JD with no extractable keywords reports 'no signal', not a failing 0%."""
    cov = keyword_coverage("Ada Lovelace\nPython\nSKILLS", "no structured sections here", MASTER_TEXT)
    assert cov.get("no_signal") is True
    assert cov["genuine_gap"] == []


def test_verify_pdf_ok(tmp_path):
    """A well-formed text layer passes the full verification gate."""
    pdf = make_pdf(tmp_path / "cv.pdf", [
        "Ada Lovelace\nLondon, UK\nada.lovelace@example.com\n+1 555 010 0199\n\n"
        "EXPERIENCE\nBuilt pipelines with Apache Airflow in Python\nSKILLS\nPython Apache Airflow"
    ])
    report = verify_pdf(pdf, {"cv": CONTACT}, JD_TEXT, 2)
    assert report.ok, report.problems
    assert report.keyword_coverage["covered"]


def test_verify_pdf_no_extractable_text(tmp_path):
    """A PDF with an empty text layer fails the gate (vector-only/font failure)."""
    blank = make_pdf(tmp_path / "blank.pdf", [""])
    report = verify_pdf(blank, {"cv": CONTACT}, JD_TEXT, 2)
    assert report.ok is False
    assert any("no extractable text" in p.lower() for p in report.problems)


def test_verify_pdf_fails_on_mojibake(tmp_path):
    """Broken glyphs in the text layer fail verification (never reach the ATS)."""
    bad = make_pdf(tmp_path / "bad.pdf", [
        "Ada Lovelace\nLondon, UK\nada.lovelace@example.com\n+1 555 010 0199\n"
        "EXPERIENCE\nBuilt \ufffd pipelines\nSKILLS\nPython"
    ])
    report = verify_pdf(bad, {"cv": CONTACT}, JD_TEXT, 2)
    assert report.ok is False
    assert report.mojibake
