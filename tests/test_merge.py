"""Unit tests for pipeline.tailor.merge highlight trimming.

Covers the impact/JD-relevance ranking introduced 2026-08-10:
- impact-first (default): competence/excellence beats JD-keyword matching
- jd_relevance preference: JD terms rank first, impact second
- reframed bullets carrying JD terms outrank verbatim copies without them
- bullets with no master analogue (fabrication risk) drop first
- master-overlap breaks ties between equally-ranked bullets
- word-boundary term matching ("SQL" never matches "SQLite")
- UP3 merge/cut: empty-highlight roles dropped when enabled; master
  highlights restored when merge is off (deterministic role set)

Run: uv run python -m tests.test_merge
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from job_search_toolkit.automation.tailor.merge import (  # noqa: E402
    _impact_score,
    _jd_relevance,
    _term_matches,
    _trim_highlights,
    extract_jd_terms,
    merge_content,
)

JD_TEXT = """\
## Technologies
- Python
- SQL
- Apache Airflow
- ClickHouse

## Competencies
- building pipelines
- data modeling
"""

JD_TERMS = extract_jd_terms(JD_TEXT)


def test_extract_jd_terms_parses_technologies_and_competencies():
    assert JD_TERMS == ["Python", "SQL", "Apache Airflow", "ClickHouse",
                        "building pipelines", "data modeling"]


def test_term_matches_word_boundary_sql_not_sqlite():
    # "SQL" must not match "SQLite"
    assert not _term_matches("Migrated data from SQLite to Postgres", "SQL")
    # ...but must match standalone "SQL"
    assert _term_matches("Wrote complex SQL queries", "SQL")


def test_term_matches_multiword_partial():
    # "Apache Airflow" matches a bullet that only says "Airflow"
    assert _term_matches("Orchestrated Airflow DAGs", "Apache Airflow")


def test_jd_relevance_fraction():
    bullet = "Built Python and SQL pipelines orchestrated with Airflow"
    # matches Python, SQL, Apache Airflow (via Airflow), building pipelines -> 4/6
    assert _jd_relevance(bullet, JD_TERMS) == 4 / 6


def test_trim_keeps_reframed_with_terms_over_verbatim_without():
    originals = [
        "Delivered workforce models at BHP with Power BI dashboards.",
        "Mentored 6 computer science students in a final-year capstone.",
        "Owned all source system connections and data ingestion.",
        "Led the remuneration review for a private equity group.",
    ]
    new = [
        "Built Python and SQL pipelines orchestrated with Airflow for fraud detection.",  # JD-relevant reframe
        "Mentored 6 computer science students in a final-year capstone.",  # verbatim, no JD terms
    ]
    kept = _trim_highlights(originals, new, JD_TERMS, max_count=1)
    assert "Airflow" in kept[0]


def test_trim_drops_no_analogue_first():
    originals = ["Delivered workforce models at BHP with Power BI dashboards."]
    new = [
        "Delivered workforce models at BHP with Power BI dashboards.",  # verbatim master
        "Designed a Mars rover guidance system with no basis in the resume.",  # fabricated
    ]
    kept = _trim_highlights(originals, new, JD_TERMS, max_count=1)
    assert "Mars" not in " ".join(kept)


def test_trim_master_overlap_tiebreak():
    originals = [
        "Delivered workforce models at BHP with Power BI dashboards.",
        "Built a data quality platform with Talend and React.",
    ]
    new = [
        "Delivered workforce models at BHP with Power BI dashboards.",  # verbatim, jd_rel=0
        "Delivered workforce models at BHP using Power BI dashboards.",  # reframe, jd_rel=0, lower overlap
    ]
    # both jd_rel=0; verbatim master bullet has higher overlap -> kept first
    kept = _trim_highlights(originals, new, JD_TERMS, max_count=1)
    assert kept[0] == new[0]


def test_trim_no_jd_terms_falls_back_to_master_overlap():
    originals = ["Delivered workforce models at BHP with Power BI dashboards."]
    new = [
        "Delivered workforce models at BHP with Power BI dashboards.",
        "Completely unrelated invented bullet about astrophysics.",
    ]
    kept = _trim_highlights(originals, new, None, max_count=1)
    assert kept[0] == new[0]


def test_trim_below_max_count_keeps_all():
    originals = ["A", "B", "C", "D"]
    new = ["A", "B"]
    assert _trim_highlights(originals, new, JD_TERMS, max_count=5) == ["A", "B"]


def test_impact_score_metric_rich_beats_plain():
    rich = "Reduced cost by 40% across 100 sources and 12 systems"
    plain = "Worked on some internal reporting"
    assert _impact_score(rich) > _impact_score(plain)


def test_trim_impact_first_keeps_impressive_over_dull_jd_match():
    originals = ["Delivered workforce models at BHP with Power BI dashboards."]
    new = [
        "Delivered workforce models at BHP with Power BI dashboards.",
        "Led remediation of 40+ sources, cut manual effort by 30%, architected "
        "the ingestion platform.",
    ]
    # impact_first: the metric-rich, strong-verb bullet wins despite lower JD overlap
    kept = _trim_highlights(originals, new, JD_TERMS, max_count=1,
                            preference="impact_first")
    assert "remediation" in kept[0]


def test_trim_jd_relevance_keeps_jd_match_over_impact():
    originals = ["Delivered workforce models at BHP with Power BI dashboards."]
    new = [
        "Built Python and SQL pipelines orchestrated with Airflow for fraud.",
        "Led remediation of 40+ sources, cut manual effort by 30%, architected "
        "the ingestion platform.",
    ]
    # jd_relevance: the Python/SQL/Airflow bullet (4/6 JD terms) wins despite
    # lower impact than the metric-rich but JD-silent alternative.
    kept = _trim_highlights(originals, new, JD_TERMS, max_count=1,
                            preference="jd_relevance")
    assert "Python" in kept[0]


def test_merge_content_cuts_empty_highlight_roles_when_enabled():
    original = {
        "cv": {"sections": {"experience": [
            {"company": "Hancock Prospecting", "highlights": ["a", "b", "c"]},
            {"company": "Modis (now Akkodis)", "highlights": ["d", "e", "f"]},
            {"company": "Deloitte Australia", "highlights": ["g", "h"]},
        ], "skills": []}}
    }
    content = {
        "summary": "x" * 60,
        "experiences": [
            {"index": 0, "highlights": ["a", "b", "c"]},
            {"index": 2, "highlights": []},  # cut by UP3
        ],
        "skills": [],
    }
    merged = merge_content(original, content, None, merge_low_value=True)
    companies = [e["company"] for e in merged["cv"]["sections"]["experience"]]
    assert "Deloitte Australia" not in companies
    assert companies == ["Hancock Prospecting", "Modis (now Akkodis)"]


def test_merge_content_empty_highlights_restore_master_when_merge_off():
    original = {
        "cv": {"sections": {"experience": [
            {"company": "Hancock Prospecting", "highlights": ["h1", "h2", "h3"]},
            {"company": "Modis (now Akkodis)", "highlights": ["m1", "m2"]},
        ], "skills": []}}
    }
    content = {
        "summary": "x" * 60,
        "experiences": [
            {"index": 0, "highlights": []},  # LLM tried to cut Hancock
            {"index": 1, "highlights": ["m1", "m2"]},
        ],
        "skills": [],
    }
    # merge off (deterministic role set): empty list means "no change" —
    # master highlights must be restored, not dropped or left blank.
    merged = merge_content(original, content, None, merge_low_value=False)
    exp = merged["cv"]["sections"]["experience"]
    assert exp[0]["company"] == "Hancock Prospecting"
    assert exp[0]["highlights"] == ["h1", "h2", "h3"]
    assert len(exp) == 2


def test_merge_content_skills_are_merged():
    original = {
        "cv": {"sections": {"experience": [
            {"company": "H", "highlights": ["h1"]},
        ], "skills": [{"label": "A", "details": "old skill"}]}}
    }
    content = {
        "summary": "x" * 60,
        "experiences": [{"index": 0, "highlights": ["h1"]}],
        "skills": [{"label": "B", "details": "new skill"}],
    }
    merged = merge_content(original, content, None, merge_low_value=False)
    assert merged["cv"]["sections"]["skills"] == [{"label": "B", "details": "new skill"}]


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
    total = sum(1 for n in globals()
                if n.startswith("test_") and callable(globals()[n]))
    print(f"\n{total - failures}/{total} passed")
    sys.exit(1 if failures else 0)
