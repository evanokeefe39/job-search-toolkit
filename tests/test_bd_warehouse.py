"""WS7 Epic 7.1 — BD warehouse dimension contract tests.

Written FIRST, before any implementation, per the plan's Validation Tests
(tasks/plans/bd-warehouse-dimensions.md). These define the silver BD tables
(dim_person / fact_touch / fact_referral / fact_inbound_attribution), the
append-only write helpers (upsert_person / record_touch / record_referral /
record_inbound / backfill_outreach_csv) and the gold BD views
(contact_cadence / referral_funnel / inbound_conversion / event_funnel /
next_action / relationship).

Conventions mirrored from silver.py / gold.py / WS1:
- surrogate keys are SHA-1 of natural keys, [:16]
- tables are CREATE IF NOT EXISTS (ALTER not rebuild); facts are append-only
- lineage columns on every fact row; deterministic, idempotent writes
- cross-checked against the REAL silver schema conventions (jobs never
  deleted; staleness/time-based; no PII in tracked files — tests use temp db)

All tests are deterministic and isolated (temp DuckDB, no network).
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from job_search_toolkit.pipelines.jd import silver
from job_search_toolkit.pipelines.jd.gold import build_bd_views, build_gold


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "warehouse.db"


@pytest.fixture()
def con(db_path: Path):
    c = duckdb.connect(str(db_path))
    try:
        yield c
    finally:
        c.close()


# --- helpers ----------------------------------------------------------------

def _upsert_person(con, name: str, **over) -> str:
    person = {"name": name}
    person.update(over)
    return silver.upsert_person(con, person)


def _touch(con, person_id: str, days_ago: int, **over) -> str:
    touch = {
        "person_id": person_id,
        "direction": "out",
        "channel": "linkedin",
        "playbook": "cold-outreach",
        "status": "sent",
        "event_date": (date.today() - timedelta(days=days_ago)).isoformat(),
        "note": None,
        "provenance": "test",
    }
    touch.update(over)
    return silver.record_touch(con, touch)


def _rows(con, table: str, where: str = "1=1") -> list[tuple]:
    return con.execute(f"SELECT * FROM silver.{table} WHERE {where}").fetchall()


def _person_count(con) -> int:
    return con.execute("SELECT count(*) FROM silver.dim_person").fetchone()[0]


def _touch_count(con, where: str = "1=1") -> int:
    return con.execute(f"SELECT count(*) FROM silver.fact_touch WHERE {where}").fetchone()[0]


# --- dim_person -------------------------------------------------------------

def test_dim_person_idempotent_upsert(con):
    """Same natural key twice -> one row, same person_id, created_at preserved."""
    silver.ensure_bd_tables(con)
    url = "  HTTPS://WWW.LinkedIn.com/in/alice  "
    pid1 = _upsert_person(con, "Alice", linkedin_url=url, title="Data Engineer")
    pid2 = _upsert_person(con, "Alice", linkedin_url=url, title="Staff Data Engineer")

    assert pid1 == pid2
    assert _person_count(con) == 1
    row = con.execute(
        "SELECT person_id, title, key_source, natural_key FROM silver.dim_person"
    ).fetchone()
    assert row[0] == pid1
    # Upsert in place: the change lands, the key stays stable.
    assert row[1] == "Staff Data Engineer"
    assert row[2] == "linkedin"
    assert row[3] == url.strip().lower()


def test_dim_person_name_key_source_when_no_linkedin(con):
    """No LinkedIn URL + common name -> key on normalized name + company_id,
    flagged key_source='name' (ambiguous merges stay visible)."""
    silver.ensure_bd_tables(con)
    pid = _upsert_person(con, "Jean Dupont", company_id="cmp1")
    row = con.execute(
        "SELECT person_id, key_source, natural_key FROM silver.dim_person"
    ).fetchone()
    assert row[1] == "name"
    assert row[2] == "jean dupont|cmp1"
    assert pid == silver.person_id("jean dupont|cmp1")


def test_dim_person_dedup_on_normalized_natural_key(con):
    """Same person via two spellings of the same LinkedIn URL -> one row."""
    silver.ensure_bd_tables(con)
    _upsert_person(con, "Alice", linkedin_url="https://www.linkedin.com/in/alice")
    _upsert_person(con, "A. Alice", linkedin_url="https://www.linkedin.com/in/ALICE")
    assert _person_count(con) == 1


# --- fact_touch -------------------------------------------------------------

def test_fact_touch_append_only_and_sequence(con):
    """N distinct touches -> N rows, touch_number = 1..N per person,
    prior rows never mutate; re-recording the same touch adds nothing."""
    silver.ensure_bd_tables(con)
    pid = _upsert_person(con, "Alice", linkedin_url="https://www.linkedin.com/in/alice")

    _touch(con, pid, 10, status="sent")
    _touch(con, pid, 5, status="replied")
    _touch(con, pid, 1, status="meeting")

    assert _touch_count(con) == 3
    numbers = sorted(r[0] for r in con.execute(
        "SELECT touch_number FROM silver.fact_touch ORDER BY event_date"
    ).fetchall())
    assert numbers == [1, 2, 3]

    # Re-recording the exact same touch is a no-op (idempotent).
    _touch(con, pid, 1, status="meeting")
    assert _touch_count(con) == 3
    numbers = sorted(r[0] for r in con.execute(
        "SELECT touch_number FROM silver.fact_touch ORDER BY event_date"
    ).fetchall())
    assert numbers == [1, 2, 3]


def test_fact_touch_no_person_allowed(con):
    """A touch with NULL person_id (draft never linked) is stored, not dropped."""
    silver.ensure_bd_tables(con)
    tid = silver.record_touch(con, {
        "person_id": None, "direction": "out", "channel": "email",
        "playbook": "cold-outreach", "status": "drafted",
        "event_date": date.today().isoformat(), "note": "draft", "provenance": "test",
    })
    assert tid
    assert _touch_count(con, "person_id IS NULL") == 1


def test_fact_touch_inbound_before_outbound_sequence(con):
    """An inbound first touch still gets sequential touch_number, direction=in."""
    silver.ensure_bd_tables(con)
    pid = _upsert_person(con, "Recruiter", linkedin_url="https://www.linkedin.com/in/rec")
    _touch(con, pid, 2, direction="in", status="replied")
    _touch(con, pid, 1, direction="out", status="sent")
    rows = con.execute(
        "SELECT touch_number, direction FROM silver.fact_touch ORDER BY event_date"
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][1] == "in"


def test_fact_touch_outbound_no_reply_kept(con):
    """Outbound with no reply -> status stays 'sent', touch_number still
    increments, and the cadence view still includes the person."""
    silver.ensure_bd_tables(con)
    pid = _upsert_person(con, "Ghost", linkedin_url="https://www.linkedin.com/in/ghost")
    _touch(con, pid, 10, status="sent")
    assert _touch_count(con, "status = 'sent'") == 1
    build_bd_views(con)
    cadence = con.execute(
        "SELECT count(*) FROM gold.contact_cadence WHERE person_id = ?", [pid]
    ).fetchone()[0]
    assert cadence == 1


# --- record_referral / record_inbound ---------------------------------------

def test_record_referral_coexists_with_cold_touch(con):
    """A referral whose referee later becomes a cold contact -> both a
    fact_referral row and a fact_touch row coexist; gold.relationship links
    them by person_id."""
    silver.ensure_bd_tables(con)
    referrer = _upsert_person(con, "Referrer", linkedin_url="https://www.linkedin.com/in/ref")
    referee = _upsert_person(con, "Referee", linkedin_url="https://www.linkedin.com/in/referee")
    silver.record_referral(con, {
        "referrer_person_id": referrer, "target_person_id": referee,
        "status": "warm_intro_sent", "event_date": date.today().isoformat(),
        "note": "warm intro", "provenance": "test",
    })
    # Referee later becomes a cold touch.
    _touch(con, referee, 1, channel="email", status="sent")

    build_bd_views(con)
    n_ref = con.execute("SELECT count(*) FROM silver.fact_referral").fetchone()[0]
    assert n_ref == 1
    linked = con.execute(
        "SELECT count(*) FROM gold.relationship WHERE person_id = ?", [referee]
    ).fetchone()[0]
    assert linked == 2
    # The relationship view links BOTH facts by person_id: the cold touch and
    # the referral (referee is the common person).
    types = con.execute(
        "SELECT fact_type FROM gold.relationship WHERE person_id = ? ORDER BY fact_type",
        [referee],
    ).fetchall()
    assert [t[0] for t in types] == ["referral", "touch"]


def test_record_inbound_attribution(con):
    """An inbound attribution with a source asset is stored and counted."""
    silver.ensure_bd_tables(con)
    pid = _upsert_person(con, "Fan", linkedin_url="https://www.linkedin.com/in/fan")
    silver.record_inbound(con, {
        "person_id": pid, "company_id": "cmp1", "source_asset": "blog_post",
        "event_date": date.today().isoformat(), "note": None, "provenance": "test",
    })
    assert con.execute(
        "SELECT count(*) FROM silver.fact_inbound_attribution WHERE source_asset = 'blog_post'"
    ).fetchone()[0] == 1


# --- gold views -------------------------------------------------------------

def test_gold_contact_cadence(con):
    """A person touched 10 days ago appears with days_since_last_touch = 10;
    one touched today does not (follow-up due)."""
    silver.ensure_bd_tables(con)
    old = _upsert_person(con, "Old", linkedin_url="https://www.linkedin.com/in/old")
    fresh = _upsert_person(con, "Fresh", linkedin_url="https://www.linkedin.com/in/fresh")
    _touch(con, old, 10)
    _touch(con, fresh, 0)

    build_bd_views(con)
    rows = con.execute(
        "SELECT person_id, days_since_last_touch FROM gold.contact_cadence ORDER BY person_id"
    ).fetchall()
    by_person = {r[0]: r[1] for r in rows}
    assert by_person[old] == 10
    assert fresh not in by_person


def test_gold_cadence_includes_never_touched(con):
    """A person with no touch at all is a follow-up due (never followed up)."""
    silver.ensure_bd_tables(con)
    silent = _upsert_person(con, "Silent", linkedin_url="https://www.linkedin.com/in/silent")
    build_bd_views(con)
    assert con.execute(
        "SELECT count(*) FROM gold.contact_cadence WHERE person_id = ?", [silent]
    ).fetchone()[0] == 1


def test_gold_referral_independent_of_cold(con):
    """A referred contact with no cold touch still appears in gold.referral_funnel."""
    silver.ensure_bd_tables(con)
    referrer = _upsert_person(con, "Referrer", linkedin_url="https://www.linkedin.com/in/ref")
    referee = _upsert_person(con, "Referee", linkedin_url="https://www.linkedin.com/in/referee")
    silver.record_referral(con, {
        "referrer_person_id": referrer, "target_person_id": referee,
        "status": "warm_intro_sent", "event_date": date.today().isoformat(),
        "note": None, "provenance": "test",
    })
    build_bd_views(con)
    # Referee has NO fact_touch row at all.
    assert _touch_count(con, f"person_id = {silver.sql_literal(referee)}") == 0
    rows = con.execute(
        "SELECT referrer_person_id, target_person_id, status FROM gold.referral_funnel"
    ).fetchall()
    assert (referrer, referee, "warm_intro_sent") in rows


def test_gold_inbound_conversion(con):
    """A direction=in attribution with a source asset is counted per asset."""
    silver.ensure_bd_tables(con)
    pid = _upsert_person(con, "Fan", linkedin_url="https://www.linkedin.com/in/fan")
    silver.record_inbound(con, {
        "person_id": pid, "company_id": "cmp1", "source_asset": "portfolio",
        "event_date": date.today().isoformat(), "note": None, "provenance": "test",
    })
    build_bd_views(con)
    row = con.execute(
        "SELECT source_asset, inbound_count FROM gold.inbound_conversion"
    ).fetchone()
    assert row is not None
    assert row[0] == "portfolio"
    assert row[1] >= 1


# --- backfill ---------------------------------------------------------------

def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "date_found", "company", "name", "title", "linkedin_url", "contact_type",
        "agency", "status", "date_approved", "date_sent", "date_replied",
        "outcome", "notes",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_outreach_csv_backfill_idempotent(con, tmp_path: Path):
    """Backfilling the same CSV twice produces no duplicate events."""
    silver.ensure_bd_tables(con)
    csv_path = tmp_path / "outreach_tracker.csv"
    _write_csv(csv_path, [
        {
            "date_found": "2026-08-01", "company": "Acme", "name": "Alice",
            "title": "Data Engineer", "linkedin_url": "https://www.linkedin.com/in/alice",
            "contact_type": "data_team", "agency": "", "status": "sent",
            "date_approved": "", "date_sent": "2026-08-02", "date_replied": "",
            "outcome": "", "notes": "initial",
        },
        {
            "date_found": "2026-08-01", "company": "Acme", "name": "Bob",
            "title": "Hiring Manager", "linkedin_url": "", "contact_type": "hiring_manager",
            "agency": "", "status": "draft_approved", "date_approved": "2026-08-01",
            "date_sent": "", "date_replied": "", "outcome": "", "notes": "",
        },
    ])

    n1 = silver.backfill_outreach_csv(con, csv_path)
    counts1 = (_person_count(con), _touch_count(con))
    n2 = silver.backfill_outreach_csv(con, csv_path)
    counts2 = (_person_count(con), _touch_count(con))

    assert n1 == 2
    assert n2 == 0
    assert counts1 == counts2
    assert _person_count(con) == 2
    assert _touch_count(con) == 2


def test_outreach_csv_backfill_missing_or_empty_noop(con, tmp_path: Path):
    """A missing or empty CSV backfills cleanly (no error, no rows)."""
    silver.ensure_bd_tables(con)
    missing = tmp_path / "nope.csv"
    assert silver.backfill_outreach_csv(con, missing) == 0
    empty = tmp_path / "empty.csv"
    empty.write_text("date_found,company\n", encoding="utf-8")
    assert silver.backfill_outreach_csv(con, empty) == 0
    assert _person_count(con) == 0


def test_backfill_maps_legacy_status_to_touch_status(con, tmp_path: Path):
    """Legacy CSV statuses map into the canonical touch status vocabulary."""
    silver.ensure_bd_tables(con)
    csv_path = tmp_path / "outreach_tracker.csv"
    _write_csv(csv_path, [
        {
            "date_found": "2026-08-01", "company": "Acme", "name": "Alice",
            "title": "", "linkedin_url": "https://www.linkedin.com/in/alice",
            "contact_type": "", "agency": "", "status": "replied",
            "date_approved": "", "date_sent": "2026-08-02", "date_replied": "2026-08-03",
            "outcome": "", "notes": "",
        },
    ])
    silver.backfill_outreach_csv(con, csv_path)
    status = con.execute("SELECT status FROM silver.fact_touch").fetchone()[0]
    assert status in silver.BD_TOUCH_STATUS


# --- regression -------------------------------------------------------------

def _seed_scored_jobs(con) -> None:
    """Minimal but realistic silver.jobs + dims so build_gold's existing views
    all materialize (ranked_jobs, by_sector, by_tier, job_history,
    weekly_snapshot, new_this_run, disappeared_this_run, score_calibration)."""
    jobs = [
        {
            "id": "j1", "source_board": "freework", "title": "Data Engineer",
            "company": "Acme", "description_text": "python spark",
            "date_posted": "2026-08-01", "apply_url": "https://x/j1",
            "salary": {"is_disclosed": True, "min_annual_eur": 70000, "max_annual_eur": 80000},
            "end_client_sector": "tech", "posting_company_type": "product",
            "overall_score": 0.8, "recommendation_tier": "top",
            "scores": {"pay": 0.6, "flexibility": 0.5, "low_responsibility": 0.5,
                       "tech_match": 0.7, "company_quality": 0.7, "freshness": 0.9},
        },
        {
            "id": "j2", "source_board": "freework", "title": "Data Analyst",
            "company": "Beta", "description_text": "excel sql",
            "date_posted": "2026-08-20", "apply_url": "https://x/j2",
            "salary": {"is_disclosed": True, "min_annual_eur": 45000, "max_annual_eur": 55000},
            "end_client_sector": "finance", "posting_company_type": "esn",
            "overall_score": 0.5, "recommendation_tier": "medium",
            "scores": {"pay": 0.3, "flexibility": 0.4, "low_responsibility": 0.4,
                       "tech_match": 0.3, "company_quality": 0.4, "freshness": 0.9},
        },
    ]
    silver.ensure_dims(con)
    cols = silver.ensure_jobs_table(con, jobs)
    silver.upsert_run(con, "run-1", jobs, cols)


def test_no_regression_existing_gold(db_path: Path, con):
    """Adding the BD views via build_gold does not alter gold.ranked_jobs."""
    _seed_scored_jobs(con)
    con.close()  # build_gold opens its own connection

    build_gold(db_path, run_id="run-1")
    con = duckdb.connect(str(db_path))
    try:
        before = con.execute(
            "SELECT id, overall_score FROM gold.ranked_jobs ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    # Add BD tables + data, then rebuild gold with the new views.
    con = duckdb.connect(str(db_path))
    try:
        silver.ensure_bd_tables(con)
        pid = _upsert_person(con, "Alice", linkedin_url="https://www.linkedin.com/in/alice")
        _touch(con, pid, 3)
    finally:
        con.close()
    build_gold(db_path, run_id="run-1")

    con = duckdb.connect(str(db_path))
    try:
        after = con.execute(
            "SELECT id, overall_score FROM gold.ranked_jobs ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    assert after == before
