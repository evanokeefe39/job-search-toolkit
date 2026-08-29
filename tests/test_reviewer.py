"""Contract tests for WS3 Epic 3.2 — adversarial drafter-reviewer.

Bounded single-revision loop behind ``--with-review``: one critique + one
targeted revision, then re-verify; "no changes" is a no-op; iteration capped;
the fabrication guard is the ceiling (a reviewer-proposed unsupported claim is
rejected). Deterministic — reviewers are injected as stubs, no network.

Validation Tests (tasks/plans/ws3-tailoring-quality.md):
- test_reviewer_bounded_loop
- test_reviewer_cannot_override_guard
"""

import sys
from copy import deepcopy
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from job_search_toolkit.automation.tailor.reviewer import (  # noqa: E402
    ReviewResult,
    bounded_revise,
    review_draft,
)


# ---------------------------------------------------------------------------
# Minimal-but-valid RenderCV master fixture (isolated, no network, no resume/).
# ---------------------------------------------------------------------------
def _master() -> dict:
    return {
        "cv": {
            "name": "Evan O'Keefe",
            "email": "evan.okeefe39@gmail.com",
            "phone": "+61 405 848 494",
            "location": "Paris, France",
            "sections": {
                "summary": ["Data engineer building secure, scalable data platforms."],
                "experience": [
                    {"company": "Hancock Prospecting", "position": "Data Engineer",
                     "highlights": ["Built data pipelines in Python",
                                    "Led a Spark migration to GCP"]},
                    {"company": "Modis", "position": "BI Developer",
                     "highlights": ["Automated Power BI reports", "Modeled SQL star schemas"]},
                ],
                "skills": [
                    {"label": "Languages", "details": "Python, SQL"},
                    {"label": "Cloud", "details": "GCP, BigQuery, Spark"},
                ],
            },
        }
    }


def _master_text() -> str:
    return yaml.safe_dump(_master(), sort_keys=False)


JD_TEXT = """## Technologies
- Python
- Apache Airflow
- GCP
## Competencies
- Data Engineering
"""


def _draft_content() -> dict:
    """First-pass TailorResponse-shaped content, merged from _master()."""
    return {
        "summary": "Data engineer with 6+ years building secure, scalable data "
                   "platforms on Python, Spark, and GCP across mining and consulting.",
        "experiences": [
            {"index": 0, "highlights": ["Built data pipelines in Python",
                                        "Led a Spark migration to GCP"]},
            {"index": 1, "highlights": ["Automated Power BI reports"]},
        ],
        "skills": [
            {"label": "Languages", "details": "Python, SQL"},
            {"label": "Cloud", "details": "GCP, BigQuery, Spark"},
        ],
    }


def _draft_dict() -> dict:
    """The full merged RenderCV dict for the first pass (as the CLI holds it)."""
    from job_search_toolkit.automation.tailor.merge import merge_content
    return merge_content(_master(), _draft_content(), JD_TEXT, merge_low_value=True)


def test_reviewer_bounded_loop_noop():
    """A reviewer returning 'no changes' is a no-op, called exactly once."""
    original = _master()
    draft = _draft_dict()
    calls = {"n": 0}

    def noop_reviewer(draft_text, master_text, jd_text, verify_text):
        calls["n"] += 1
        return ReviewResult(critique="Framing is solid.", revision=None, changed=False)

    final, review = bounded_revise(original, draft, _master_text(), JD_TEXT, noop_reviewer)
    assert calls["n"] == 1, "the reviewer must be called exactly once"
    assert review.changed is False
    assert final == draft, "a no-change reviewer must not alter the draft"


def test_reviewer_bounded_loop_single_revise():
    """Exactly one revise pass is applied; the loop does not iterate further."""
    original = _master()
    draft = _draft_dict()
    calls = {"n": 0}

    def revising_reviewer(draft_text, master_text, jd_text, verify_text):
        calls["n"] += 1
        # Targeted revision: surface a supported-but-missing keyword (Airflow),
        # which the JD lists and the draft missed.
        revision = deepcopy(_draft_content())
        revision["skills"] = [
            {"label": "Languages", "details": "Python, SQL"},
            {"label": "Orchestration", "details": "Airflow, Spark"},
        ]
        return ReviewResult(critique="Add orchestration keywords.", revision=revision,
                            changed=True)

    final, review = bounded_revise(original, draft, _master_text(), JD_TEXT, revising_reviewer)
    assert calls["n"] == 1, "exactly one revise pass, never more"
    assert review.changed is True
    assert final != draft
    assert final["cv"]["sections"]["skills"] == [
        {"label": "Languages", "details": "Python, SQL"},
        {"label": "Orchestration", "details": "Airflow, Spark"},
    ]


def test_reviewer_cannot_override_guard():
    """A reviewer-proposed fabricated claim is rejected by the fabrication guard."""
    original = _master()
    draft = _draft_dict()
    calls = {"n": 0}

    def hallucinating_reviewer(draft_text, master_text, jd_text, verify_text):
        calls["n"] += 1
        # "Quantum Computing" is absent from the master — a fabricated skill.
        revision = deepcopy(_draft_content())
        revision["skills"] = [
            {"label": "Languages", "details": "Python, SQL, Quantum Computing"},
        ]
        return ReviewResult(critique="Add quantum expertise.", revision=revision,
                            changed=True)

    final, review = bounded_revise(original, draft, _master_text(), JD_TEXT,
                                   hallucinating_reviewer)
    assert calls["n"] == 1
    assert review.changed is True
    # The guard is the ceiling: the fabricated claim must NOT land.
    assert not any("quantum" in str(s).lower() for s in final["cv"]["sections"]["skills"])
    assert final == draft, "a rejected revision must leave the first pass intact"


def test_review_draft_parses_llm_response():
    """review_draft maps a ReviewerResponse into a ReviewResult (via llm_caller)."""
    calls = {"n": 0}

    async def fake_llm(system, user, **kw):
        calls["n"] += 1
        assert kw.get("output_type").__name__ == "ReviewerResponse"
        return {
            "critique": "Summary underplays GCP; add it to the first line.",
            "revision": _draft_content(),
        }

    res = review_draft("draft yaml", "master text", JD_TEXT, "verify report",
                       model_name="m", base_url="b", api_key="k", llm_caller=fake_llm)
    assert calls["n"] == 1
    assert res.changed is True
    assert "GCP" in res.critique
    assert res.revision == _draft_content()


def test_review_draft_no_changes_when_no_revision():
    """A reviewer response with no revision is a no-op ReviewResult."""
    calls = {"n": 0}

    async def fake_llm(system, user, **kw):
        calls["n"] += 1
        return {"critique": "No changes needed.", "revision": None}

    res = review_draft("draft yaml", "master text", JD_TEXT, "verify report",
                       model_name="m", base_url="b", api_key="k", llm_caller=fake_llm)
    assert calls["n"] == 1
    assert res.changed is False
    assert res.revision is None
