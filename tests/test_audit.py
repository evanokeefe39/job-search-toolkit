"""Unit tests for pipeline.tailor.audit fabrication checking.

Covers merge_low_value subset semantics (UP3) and reworded JD-term matching
introduced 2026-08-10:
- equal-count company rename flagged even when merge is on
- cutting a low-value role is allowed when merge is on, HARD when off
- reworded JD competencies ("monitoring pipelines" -> "pipeline monitoring")
  classify as JD-derived, not fabricated

Run: uv run python -m tests.test_audit
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from job_search_toolkit.automation.tailor.audit import (  # noqa: E402
    _jd_term_reworded,
    _words,
    check_fabrication,
)

JD_TEXT = """\
## Technologies
- Python
- SQL

## Competencies
- monitoring pipelines
- integrating models
"""


def _exp(companies: list[str]) -> dict:
    return {"cv": {"sections": {"experience": [
        {"company": c} for c in companies
    ], "skills": []}}}


def test_words_significant_only():
    assert _words("pipeline monitoring") == {"pipeline", "monitoring"}
    assert "the" not in _words("the quick fox")


def test_jd_term_reworded_order_insensitive():
    assert _jd_term_reworded("pipeline monitoring", JD_TEXT)
    assert _jd_term_reworded("model integration", JD_TEXT)
    assert not _jd_term_reworded("quantum cryptography", JD_TEXT)


def test_equal_count_rename_flagged_when_merge_on():
    orig = _exp(["Hancock Prospecting", "Modis"])
    t = _exp(["Hancock Prospecting", "EvilCorp"])
    hard, _ = check_fabrication(orig, t, "master text", "jd text",
                                merge_low_value=True)
    assert any("EvilCorp" in h for h in hard)


def test_cut_allowed_when_merge_on():
    orig = _exp(["Hancock Prospecting", "Modis", "Deloitte"])
    t = _exp(["Hancock Prospecting", "Modis"])
    hard, _ = check_fabrication(orig, t, "master text", "jd text",
                                merge_low_value=True)
    assert hard == []


def test_cut_hard_when_merge_off():
    orig = _exp(["Hancock Prospecting", "Modis", "Deloitte"])
    t = _exp(["Hancock Prospecting", "Modis"])
    hard, _ = check_fabrication(orig, t, "master text", "jd text",
                                merge_low_value=False)
    assert any("Exp count" in h for h in hard)


def test_reworded_jd_term_is_jd_derived_not_hard():
    orig = {"cv": {"sections": {"experience": [], "skills": [
        {"label": "Data Eng", "details": "data pipelines"},
    ]}}}
    t = {"cv": {"sections": {"experience": [], "skills": [
        {"label": "Data Eng", "details": "data pipelines, pipeline monitoring"},
    ]}}}
    hard, jd_adds = check_fabrication(orig, t, "data pipelines", JD_TEXT)
    assert hard == []
    assert "pipeline monitoring" in jd_adds
def test_metric_fabrication_still_hard():
    orig = {"cv": {"sections": {"experience": [], "skills": []}}}
    t = {"cv": {"sections": {"experience": [], "skills": [
        {"label": "X", "details": "improved performance by 40%"},
    ]}}}
    hard, _ = check_fabrication(orig, t, "no metrics here", "jd text")
    assert any("metric" in h.lower() for h in hard)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    total = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\n{total - failures}/{total} passed")
    sys.exit(1 if failures else 0)
