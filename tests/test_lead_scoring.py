"""WS7 Epic 7.2 — Lead scoring contract tests.

Written FIRST, before any implementation, per the plan's Validation Tests
(tasks/plans/lead-scoring.md). These define the deterministic, zero-LLM lead
scoring core (score_leads) as a second consumer of score_engine's weighted
feature machinery, the versioned lead-weight config, weight validation, and
the SQL-evidenced, gated calibration path.

Hard gates preserved from WS1:
- job ranking is bit-for-bit frozen (guarded by test_job_score_bit_for_bit in
  tests/test_score_engine.py — the refactor must not alter score_jobs).
- an LLM never proposes or applies weights; weights change only via the
  gated calibration path with SQL evidence.

All tests deterministic, isolated (temp DuckDB / temp config files), no network.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from job_search_toolkit.pipelines.jd import score_engine as se


# --- deterministic core -----------------------------------------------------

def _base_lead(**over) -> dict:
    """A lead with full, non-missing signals."""
    lead = {
        "person_id": "p1",
        "company_id": "cmp1",
        "sector_overlap": 0.8,
        "tech_overlap": 0.6,
        "has_referral": False,
        "recent_touch_days": None,
        "inbound_touch": False,
        "funding_amount_usd": 10_000_000,
        "active_hiring": True,
        "contact_available": True,
    }
    lead.update(over)
    return lead


def _score_one(**over) -> dict:
    return se.score_leads([_base_lead(**over)])[0]


def test_lead_score_deterministic():
    """Same lead inputs twice -> identical lead_score and dimension scores."""
    a = _score_one()
    b = _score_one()
    assert a["lead_score"] == b["lead_score"]
    assert a["lead_scores"] == b["lead_scores"]


def test_lead_score_in_0_1_and_sums_weights():
    """lead_score is a deterministic weighted sum over the 4 dimensions."""
    lead = _score_one()
    assert 0.0 <= lead["lead_score"] <= 1.0
    dims = lead["lead_scores"]
    assert set(dims) == {"intent", "fit", "access", "urgency"}
    assert all(0.0 <= v <= 1.0 for v in dims.values())
    assert lead["lead_score"] == round(
        sum(dims[k] * se.LEAD_WEIGHTS[k] for k in se.LEAD_FEATURES), 3
    )


def test_lead_score_missing_signal_neutral():
    """A lead with every signal missing scores neutral (0.5) on those
    dimensions; the total is 0.5, never 0, never a crash."""
    sparse = _score_one(
        sector_overlap=None, tech_overlap=None, funding_amount_usd=None,
        recent_touch_days=None, contact_available=None, active_hiring=None,
        inbound_touch=False, has_referral=False,
    )
    assert sparse["lead_score"] == 0.5
    assert all(v == 0.5 for v in sparse["lead_scores"].values())


def test_referral_boost():
    """An otherwise-identical lead with a fact_referral (has_referral=True)
    scores higher than the cold equivalent (access dimension)."""
    cold = _score_one(has_referral=False)
    warm = _score_one(has_referral=True)
    assert warm["lead_scores"]["access"] > cold["lead_scores"]["access"]
    assert warm["lead_score"] > cold["lead_score"]


def test_inbound_baseline():
    """A single inbound touch yields a higher intent baseline than a
    never-touched cold lead (inbound converts at a higher rate)."""
    cold = _score_one(inbound_touch=False, sector_overlap=0.5)
    inbound = _score_one(inbound_touch=True, sector_overlap=0.5)
    assert inbound["lead_scores"]["intent"] > cold["lead_scores"]["intent"]
    assert inbound["lead_score"] > cold["lead_score"]


# --- weight validation ------------------------------------------------------

def test_weights_must_sum_to_one():
    """Config validation rejects weights that do not sum to 1.0."""
    with pytest.raises(ValueError):
        se.validate_lead_weights({"intent": 0.5, "fit": 0.5, "access": 0.1, "urgency": 0.1})


def test_score_leads_rejects_bad_weights():
    with pytest.raises(ValueError):
        se.score_leads([_base_lead()], weights={"intent": 1.0})


# --- calibration gating -----------------------------------------------------

def test_calibration_gated_no_apply_without_evidence(tmp_path: Path):
    """Weight change is not applied without --apply-calibration; the gated
    apply on an empty warehouse refuses (not enough data) and writes no file."""
    db_path = tmp_path / "warehouse.db"
    # Empty warehouse: no leads, no outcome evidence.
    con = duckdb.connect(str(db_path))
    try:
        se.ensure_lead_table(con)
    finally:
        con.close()

    with pytest.raises(RuntimeError, match="not enough data"):
        se.lead_apply_calibration(db_path)

    # Nothing was written to the active-override file.
    active = tmp_path / "lead_active.yaml"
    assert not active.exists()


def test_active_override_changes_lead_weights(monkeypatch, tmp_path: Path):
    """score_leads honours the versioned active-override file (the only path
    calibration may write), so weights only change via that gated file."""
    default = _score_one()["lead_score"]

    import yaml

    override = tmp_path / "lead_active.yaml"
    override.write_text(
        yaml.safe_dump({"version": 2, "weights": {
            "intent": 1.0, "fit": 0.0, "access": 0.0, "urgency": 0.0,
        }}),
        encoding="utf-8",
    )
    monkeypatch.setenv("JST_LEAD_ACTIVE_WEIGHTS_FILE", str(override))

    # force re-load from the override file
    se.LEAD_WEIGHTS = se._load_lead_weights()
    try:
        boosted = _score_one()["lead_score"]
    finally:
        se.LEAD_WEIGHTS = se._load_default_lead_weights()
    assert boosted != default


def test_lead_score_calibration_view_surfaces_bands(tmp_path: Path):
    """gold.lead_score_calibration surfaces lead-score band distributions with
    counts (outcome link gated until outcomes exist)."""
    from job_search_toolkit.pipelines.jd.gold import build_bd_views

    db_path = tmp_path / "warehouse.db"
    con = duckdb.connect(str(db_path))
    try:
        se.ensure_lead_table(con)
        se.upsert_lead_scores(con, [_base_lead(), _base_lead(person_id="p2")])
        build_bd_views(con)
        rows = con.execute(
            "SELECT lead_count FROM gold.lead_score_calibration"
        ).fetchall()
    finally:
        con.close()
    # Both scored leads appear across the score bands (counts sum to 2).
    assert sum(r[0] for r in rows) == 2


def test_score_leads_from_warehouse_populates_lead(tmp_path: Path):
    """The warehouse producer turns BD tables into silver.lead + gold.lead_rank."""
    from job_search_toolkit.pipelines.jd import silver
    from job_search_toolkit.pipelines.jd.gold import build_bd_views

    db = tmp_path / "warehouse.db"
    con = duckdb.connect(str(db))
    try:
        silver.ensure_dims(con)  # dim_company join target (created by the pipeline)
        silver.ensure_bd_tables(con)
        p1 = silver.upsert_person(con, {"name": "A", "linkedin_url": "https://x/a", "title": "DE"})
        p2 = silver.upsert_person(con, {"name": "B", "linkedin_url": "https://x/b", "title": "HM"})
        # p1 is inbound (direction=in); p2 is a recent cold outbound.
        silver.record_touch(con, {
            "person_id": p1, "direction": "in", "channel": "linkedin",
            "status": "replied", "event_date": "2026-08-01", "provenance": "test",
        })
        silver.record_touch(con, {
            "person_id": p2, "direction": "out", "channel": "linkedin",
            "status": "sent", "event_date": "2026-08-26", "provenance": "test",
        })
        n = se.score_leads_from_warehouse(con)
        assert n == 2
        build_bd_views(con)
        rows = con.execute(
            "SELECT person_id, lead_score FROM gold.lead_rank ORDER BY lead_score DESC"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] >= rows[1][1]  # deterministic, ranked DESC
    finally:
        con.close()
