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
            "tech_match": 0.6,
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
    S.ensure_dims(con)
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
    assert row == ("j1", "run1", "run1", True, None, S.get_enrichment_version())


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


def test_upsert_does_not_deactivate_unseen_jobs(wh):
    """Subset runs must not deactivate boards outside the run (staleness model)."""
    con, _ = wh
    _upsert(con, "run1", [make_job("j1"), make_job("j2")])
    _upsert(con, "run2", [make_job("j2")])  # j1 not in this run
    rows = con.execute("SELECT id, is_active FROM silver.jobs ORDER BY id").fetchall()
    assert rows == [("j1", True), ("j2", True)]  # nothing deactivated


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


def test_dim_company_gate_freework_only(wh):
    """hiringcafe company data comes from the source — never LLM-researched.
    The dim gate selects only non-hiringcafe companies whose org_type is NULL
    (one row per company, not per job)."""
    con, _ = wh
    _upsert(con, "run1", [
        make_job("fw", board="freework", org_type=None),
        make_job("hc", board="hiringcafe", org_type="unknown"),
    ])
    rows = con.execute(
        f"SELECT source_board FROM silver.dim_company WHERE {S.DIM_COMPANY_GATE}"
    ).fetchall()
    assert [r[0] for r in rows] == ["freework"]


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


def test_mark_enriched_ignores_dim_company_research(wh):
    """Company research is dimension-scoped — a freework row with org_type
    NULL in dim_company is still row-enriched once translate/tech/classify
    pass (company data no longer gates enriched_at)."""
    con, _ = wh
    _upsert(con, "run1", [make_job(board="freework", org_type=None, scored=True)])
    S.mark_enriched(con)
    row = con.execute("SELECT enriched_at IS NOT NULL, overall_score FROM silver.jobs").fetchone()
    assert row == (True, None)  # enriched; score cleared so score stage re-runs


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
    """new_this_run / disappeared_this_run / ranked_jobs reflect staleness."""
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(S, "WAREHOUSE_DB", db)
    con = S.connect()
    # Run 1: j1 + j2. Run 2: only j2 (j1 stops being seen). j3 new in run 2.
    _upsert(con, "run1", [make_job("j1"), make_job("j2")])
    _upsert(con, "run2", [make_job("j2"), make_job("j3")])
    # j1 is now stale: it was never re-scraped, so age its last_seen beyond the
    # staleness horizon. Nothing is deactivated (is_active stays TRUE).
    con.execute(
        "UPDATE silver.jobs SET last_seen_at = NOW() - INTERVAL 90 DAY WHERE id = 'j1'"
    )

    build_gold(db, run_id="run2")

    new = con.execute("SELECT id FROM gold.new_this_run ORDER BY id").fetchall()
    assert [r[0] for r in new] == ["j3"]
    gone = con.execute("SELECT id FROM gold.disappeared_this_run ORDER BY id").fetchall()
    assert [r[0] for r in gone] == ["j1"]  # stale, not deactivated
    ranked = con.execute("SELECT id FROM gold.ranked_jobs ORDER BY id").fetchall()
    assert [r[0] for r in ranked] == ["j2", "j3"]  # scored + non-stale only
    history = con.execute(
        "SELECT id, first_seen_run, last_seen_run, is_active FROM gold.job_history "
        "ORDER BY id"
    ).fetchall()
    assert history == [
        ("j1", "run1", "run1", True),
        ("j2", "run1", "run2", True),
        ("j3", "run2", "run2", True),
    ]
    con.close()


# ---------------------------------------------------------------------------
# Kimball dims
# ---------------------------------------------------------------------------

def test_ensure_dims_creates_tables_and_seeds_board(wh):
    con, _ = wh
    S.ensure_dims(con)
    # Count tracks silver.BOARD_DIMENSIONS: 2 new opt-in boards (wttj, builtin) added.
    assert con.execute("SELECT COUNT(*) FROM silver.dim_board").fetchone()[0] == 12
    for bid in S.BOARD_DIMENSIONS:
        assert con.execute(
            f"SELECT COUNT(*) FROM silver.dim_board WHERE board_id = '{bid}'"
        ).fetchone()[0] == 1
    # dim tables exist even before any upsert
    assert con.execute("SELECT COUNT(*) FROM silver.dim_company").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM silver.dim_date").fetchone()[0] == 0


def test_dim_company_one_row_per_name_board(wh):
    """Same normalized name on two boards = two dim rows (cross-board dedup
    is out of scope); same board+name = one row."""
    con, _ = wh
    _upsert(con, "run1", [
        make_job("j1", board="freework"),
        make_job("j2", board="freework"),
        make_job("j3", board="hiringcafe"),
    ])
    rows = con.execute(
        "SELECT name, source_board FROM silver.dim_company ORDER BY source_board"
    ).fetchall()
    assert rows == [("acme", "freework"), ("acme", "hiringcafe")]


def test_dim_company_id_stable_and_board_scoped():
    assert S.company_id("Acme", "freework") == S.company_id("acme", "freework")
    assert S.company_id("Acme", "freework") != S.company_id("Acme", "hiringcafe")
    assert S.company_id("  Acme  Consulting ", "freework") == S.company_id(
        "acme consulting", "freework"
    )


def test_dim_date_spine_from_date_posted(wh):
    con, _ = wh
    _upsert(con, "run1", [
        make_job("j1"),
        make_job("j2"),
    ])
    con.execute("UPDATE silver.jobs SET date_posted = '2026-07-01' WHERE id = 'j2'")
    S.refresh_dim_date(con)
    rows = con.execute(
        "SELECT date_id, year, quarter FROM silver.dim_date ORDER BY date_id"
    ).fetchall()
    assert rows == [("2026-07-01", 2026, 3), ("2026-08-01", 2026, 3)]


def test_join_company_rebuilds_company_info(wh):
    con, _ = wh
    _upsert(con, "run1", [make_job("j1", board="freework")])
    con.execute(
        "UPDATE silver.dim_company SET org_type = 'consulting_firm', "
        "stock_symbol = 'ACME' WHERE source_board = 'freework'"
    )
    jobs = S.fetch_jobs(con, ["id", "company_id"], "is_active", join_company=True)
    assert len(jobs) == 1
    ci = jobs[0]["company_info"]
    assert ci["name"] == "Acme"
    assert ci["org_type"] == "consulting_firm"
    assert ci["stock_symbol"] == "ACME"
    assert ci["industry"] == []


def test_migrate_company_info_drops_column_and_seeds_dim(wh):
    """Legacy warehouse: company_info JSON column present. ensure_dims migrates
    it into dim_company, sets company_id, and drops the column."""
    con, _ = wh
    _upsert(con, "run1", [make_job("j1", board="freework")])
    # Simulate the legacy column existing with research data.
    con.execute("ALTER TABLE silver.jobs ADD COLUMN company_info JSON")
    con.execute(
        "UPDATE silver.jobs SET company_info = "
        "'{\"name\": \"Acme\", \"org_type\": \"consulting_firm\"}'::JSON"
    )
    # Rebuild the legacy shape: company_id column exists but is NULL now —
    # the migration backfills it.
    con.execute("UPDATE silver.jobs SET company_id = NULL")

    S.ensure_dims(con)

    cols = {r[1] for r in con.execute("PRAGMA table_info('silver.jobs')").fetchall()}
    assert "company_info" not in cols
    row = con.execute(
        "SELECT org_type, stock_symbol FROM silver.dim_company WHERE source_board = 'freework'"
    ).fetchone()
    assert row == ("consulting_firm", None)
    cid = con.execute("SELECT company_id FROM silver.jobs WHERE id = 'j1'").fetchone()[0]
    assert cid == S.company_id("Acme", "freework")


class TestMergeCompanyCI:
    """Unit tests for _merge_company_ci — the per-company canonicalization rule."""

    def test_picks_most_recent_non_null(self):
        newer = ("2026-08-10", {"name": "Acme", "size_employees": 500})
        older = ("2026-08-01", {"name": "Acme", "size_employees": 200, "hq_country": "FR"})
        merged = S._merge_company_ci([newer, older])
        assert merged == {"name": "Acme", "size_employees": 500, "hq_country": "FR"}

    def test_null_last_seen_is_oldest(self):
        with_null = (None, {"org_type": "private"})
        with_date = ("2026-08-10", {"org_type": "public"})
        merged = S._merge_company_ci([with_date, with_null])
        assert merged["org_type"] == "public"

    def test_null_value_does_not_block(self):
        newer = ("2026-08-10", {"org_type": None})
        older = ("2026-08-01", {"org_type": "public"})
        merged = S._merge_company_ci([newer, older])
        assert merged["org_type"] == "public"

    def test_first_non_null_wins_no_data_gap(self):
        a = ("2026-08-05", {"hq_country": "FR"})
        b = ("2026-08-01", {"homepage_url": "acme.com"})
        merged = S._merge_company_ci([a, b])
        assert merged == {"hq_country": "FR", "homepage_url": "acme.com"}


def test_migration_merge_picks_newer_research(wh):
    """Two jobs of the same company with different research snapshots.

    The migration must canonicalize per-company, preferring the most recent
    row's values but never losing data (field-wise merge: an older row's value
    survives when the newer row has NULL for that field).
    """
    con, _ = wh
    j1 = make_job("j1", board="freework", title="Eng1", lang="en")
    j2 = make_job("j2", board="freework", title="Eng2", lang="en")
    _upsert(con, "run1", [j1, j2])

    con.execute("UPDATE silver.jobs SET last_seen_at = '2026-08-01' WHERE id = 'j1'")
    con.execute("UPDATE silver.jobs SET last_seen_at = '2026-08-10' WHERE id = 'j2'")

    con.execute("ALTER TABLE silver.jobs ADD COLUMN company_info JSON")
    con.execute(
        "UPDATE silver.jobs SET company_info = "
        "'{\"name\": \"Acme\", \"org_type\": \"private\", \"size_employees\": 200}'::JSON "
        "WHERE id = 'j1'"
    )
    con.execute(
        "UPDATE silver.jobs SET company_info = "
        "'{\"name\": \"Acme\", \"org_type\": \"public\", \"homepage_url\": \"acme.com\"}'::JSON "
        "WHERE id = 'j2'"
    )
    con.execute("UPDATE silver.jobs SET company_id = NULL")

    S.ensure_dims(con)

    cols = {r[1] for r in con.execute("PRAGMA table_info('silver.jobs')").fetchall()}
    assert "company_info" not in cols
    row = con.execute(
        "SELECT org_type, size_employees, homepage_url "
        "FROM silver.dim_company WHERE source_board = 'freework'"
    ).fetchone()
    assert row == ("public", 200, "acme.com")
    for jid in ("j1", "j2"):
        cid = con.execute(
            f"SELECT company_id FROM silver.jobs WHERE id = '{jid}'"
        ).fetchone()[0]
        assert cid == S.company_id("Acme", "freework")



def test_exports_company_info_json_regression(wh):
    """The export bridges' _COMPANY_INFO_JSON constant must compile in duckdb 1.5.5.

    Regression for the Postgres-style 'key': value syntax bug — DuckDB
    requires positional 'key', value pairs. This test executes the actual
    SQL constant against the warehouse so syntax errors are caught statically.
    """
    import json

    from job_search_toolkit.pipelines.jd.assets.exports import _COMPANY_INFO_JSON

    con, _ = wh
    _upsert(con, "run1", [make_job("j1", board="freework")])
    con.execute(
        "UPDATE silver.dim_company SET org_type = 'private', "
        "stock_symbol = 'ACME', size_employees = 500, homepage_url = 'acme.com'"
    )
    sql = (
        "SELECT j.id, j.source_board, j.company_id, "
        + _COMPANY_INFO_JSON
        + " FROM silver.jobs j LEFT JOIN silver.dim_company c "
        "ON j.company_id = c.company_id"
    )
    row = con.execute(sql).fetchone()
    ci = row[3]
    if isinstance(ci, str):
        ci = json.loads(ci)
    assert ci["name"] == "Acme"
    assert ci["org_type"] == "private"
    assert ci["stock_symbol"] == "ACME"
    assert ci["size_employees"] == 500
    assert ci["homepage_url"] == "acme.com"
    assert ci["industry"] == []
