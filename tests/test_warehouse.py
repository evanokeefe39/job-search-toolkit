"""Unit tests for the medallion warehouse (silver.py + gold.py).

Run: uv run python -m pytest tests/test_warehouse.py -q
"""

from __future__ import annotations

import pytest

from job_search_toolkit.pipelines.jd import silver as S
from job_search_toolkit.pipelines.jd.gold import build_gold


_UNSET = object()


def make_job(
    jid: str = "j1",
    board: str = "freework",
    title: str = "Engineer",
    lang: str = "en",
    desc: str = "text",
    tech: list[str] | None | object = _UNSET,
    engagement: str | None = None,
    org_type: str | None = None,
    scored: bool = True,
) -> dict:
    """A minimal canonical record. Defaults to a fully-enriched freework row."""
    if tech is _UNSET:
        tech = ["Python"]
    engagement = engagement or ("direct" if board == "hiringcafe" else "consulting")
    # NULL = not yet LLM-processed (matches adapt_freework semantics).
    org_type = org_type if org_type is not None else ("private" if board == "hiringcafe" else None)
    job: dict = {
        "id": jid,
        "source_board": board,
        "title": title,
        "description_text": desc,
        "description_language": lang,
        "technologies": tech,
        "competencies": ["sql"],
        "seniority_level": "senior",
        "role_category": "data_engineer",
        "salary": {
            "min_annual_eur": 60000.0,
            "max_annual_eur": 80000.0,
            "currency_original": "EUR",
            "frequency_original": "yearly",
            "is_disclosed": True,
        },
        "workplace_type": "remote",
        "contract_types": ["contract"],
        "contract_duration": None,
        "company": "Acme",
        "apply_url": f"https://x/{jid}",
        "location_raw": "Paris",
        "date_posted": "2026-08-01",
        "company_info": {"name": "Acme", "org_type": org_type},
        "engagement_type": engagement,
        "posting_company_type": "end_client",
        "end_client_name": None,
        "end_client_sector": None,
        "views": 10,
        "applications": 2,
        "is_expired": False,
        "years_experience_min": 3,
    }
    if scored:
        job["scores"] = {
            "pay": 0.6, "flexibility": 0.8, "low_responsibility": 0.7,
            "tech_match": 0.6, "company_quality": 0.5,
        }
        job["overall_score"] = 0.62
        job["recommendation_tier"] = "high"
    return job


@pytest.fixture
def wh(tmp_path, monkeypatch):
    """A warehouse on a throwaway DB file, with WAREHOUSE_DB pointed at it."""
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(S, "WAREHOUSE_DB", db)
    con = S.connect()
    yield con, db
    con.close()


def _upsert(con, run_id: str, jobs: list[dict], full_replace: bool = False):
    columns = S.ensure_jobs_table(con, jobs)
    S.upsert_run(con, run_id, jobs, columns, full_replace=full_replace)
    return columns


# ---------------------------------------------------------------------------
# Upsert lineage
# ---------------------------------------------------------------------------

def test_upsert_inserts_with_lineage(wh):
    con, _ = wh
    _upsert(con, "run1", [make_job(scored=False)])
    row = con.execute(
        "SELECT id, first_seen_run, last_seen_run, is_active, enriched_at, "
        "enrichment_version FROM silver.jobs"
    ).fetchone()
    assert row == ("j1", "run1", "run1", True, None, S.ENRICHMENT_VERSION)


def test_reupsert_preserves_enrichment(wh):
    """Re-scrape must not clobber enrichment columns (the INSERT OR REPLACE trap)."""
    con, _ = wh
    _upsert(con, "run1", [make_job(scored=False)])
    con.execute(
        "UPDATE silver.jobs SET technologies='[\"Spark\"]'::JSON, enriched_at=NOW() "
        "WHERE id='j1'"
    )
    _upsert(con, "run2", [make_job(scored=False)])
    row = con.execute(
        "SELECT first_seen_run, last_seen_run, is_active, enriched_at IS NOT NULL, "
        "json_extract_string(technologies, '$[0]') FROM silver.jobs"
    ).fetchone()
    assert row == ("run1", "run2", True, True, "Spark")


def test_full_replace_adopts_content_keeps_first_seen(wh):
    con, _ = wh
    shadow = make_job(desc="", tech=[])
    shadow["description_text"] = ""
    _upsert(con, "prior", [shadow])
    full = make_job(desc="real description", tech=["Spark"])
    _upsert(con, "migration", [full], full_replace=True)
    row = con.execute(
        "SELECT first_seen_run, last_seen_run, description_text, "
        "json_extract_string(technologies, '$[0]') FROM silver.jobs"
    ).fetchone()
    assert row == ("prior", "migration", "real description", "Spark")


def test_deactivate_not_seen(wh):
    con, _ = wh
    _upsert(con, "run1", [make_job("j1"), make_job("j2")])
    _upsert(con, "run2", [make_job("j2")])
    S.deactivate_not_seen(con, "run2")
    rows = con.execute("SELECT id, is_active FROM silver.jobs ORDER BY id").fetchall()
    assert rows == [("j1", False), ("j2", True)]


# ---------------------------------------------------------------------------
# Enrichment gates
# ---------------------------------------------------------------------------

def test_translate_gate_selects_french_only(wh):
    con, _ = wh
    _upsert(con, "run1", [make_job("en", lang="en"), make_job("fr", lang="fr")])
    rows = S.fetch_jobs(con, ["id", "description_text"], S.GATE_TRANSLATE)
    assert [r["id"] for r in rows] == ["fr"]


def test_tech_gate_selects_null_only(wh):
    """Empty is a terminal LLM result — only NULL rows are re-selected."""
    con, _ = wh
    _upsert(con, "run1", [
        make_job("full", tech=["Python"]),
        make_job("empty", tech=[]),
        make_job("null_tech", tech=None),
    ])
    rows = S.fetch_jobs(con, ["id"], S.GATE_TECH)
    assert [r["id"] for r in rows] == ["null_tech"]


def test_company_gate_freework_only(wh):
    """hiringcafe company data comes from the source — never LLM-researched.
    A freework row with 'unknown' org_type (an LLM result) is terminal."""
    con, _ = wh
    _upsert(con, "run1", [
        make_job("fw", board="freework", org_type=None),
        make_job("hc", board="hiringcafe", org_type="unknown"),
    ])
    rows = S.fetch_jobs(con, ["id", "source_board"], S.GATE_COMPANY)
    assert [(r["id"], r["source_board"]) for r in rows] == [("fw", "freework")]


def test_mark_enriched_when_all_gates_pass(wh):
    con, _ = wh
    # Fully-enriched hiringcafe row (all gates pass, no LLM stage needed).
    _upsert(con, "run1", [make_job(board="hiringcafe", scored=True)])
    assert con.execute("SELECT enriched_at FROM silver.jobs").fetchone()[0] is None
    S.mark_enriched(con)
    row = con.execute(
        "SELECT enriched_at IS NOT NULL, overall_score FROM silver.jobs"
    ).fetchone()
    # enriched_at set, and overall_score cleared so the score stage re-runs.
    assert row == (True, None)


def test_mark_enriched_keeps_pending_when_company_missing(wh):
    con, _ = wh
    # Freework row with org_type NULL (the deferred company research case).
    _upsert(con, "run1", [make_job(board="freework", org_type=None, scored=True)])
    S.mark_enriched(con)
    row = con.execute("SELECT enriched_at IS NOT NULL, overall_score FROM silver.jobs").fetchone()
    assert row == (False, 0.62)  # still pending, score preserved


def test_gates_skip_inactive_rows(wh):
    con, _ = wh
    _upsert(con, "run1", [make_job("j1", lang="fr", tech=[])])
    con.execute("UPDATE silver.jobs SET is_active = FALSE WHERE id = 'j1'")
    assert S.fetch_jobs(con, ["id"], S.GATE_TRANSLATE) == []
    assert S.fetch_jobs(con, ["id"], S.GATE_TECH) == []


# ---------------------------------------------------------------------------
# Gold views
# ---------------------------------------------------------------------------

def test_gold_views_delta(tmp_path, monkeypatch):
    """Two runs: new_this_run / disappeared_this_run / ranked_jobs behave."""
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(S, "WAREHOUSE_DB", db)
    con = S.connect()
    # Run 1: j1 + j2. Run 2: only j2 (j1 disappears). j3 is brand new in run 2.
    _upsert(con, "run1", [make_job("j1"), make_job("j2")])
    _upsert(con, "run2", [make_job("j2"), make_job("j3")])
    S.deactivate_not_seen(con, "run2")

    build_gold(db, run_id="run2")

    new = con.execute("SELECT id FROM gold.new_this_run ORDER BY id").fetchall()
    assert [r[0] for r in new] == ["j3"]
    gone = con.execute("SELECT id FROM gold.disappeared_this_run ORDER BY id").fetchall()
    assert [r[0] for r in gone] == ["j1"]
    ranked = con.execute("SELECT id FROM gold.ranked_jobs ORDER BY id").fetchall()
    assert [r[0] for r in ranked] == ["j2", "j3"]  # active + scored only
    history = con.execute(
        "SELECT id, first_seen_run, last_seen_run, is_active FROM gold.job_history "
        "ORDER BY id"
    ).fetchall()
    assert history == [
        ("j1", "run1", "run1", False),
        ("j2", "run1", "run2", True),
        ("j3", "run2", "run2", True),
    ]
    con.close()
