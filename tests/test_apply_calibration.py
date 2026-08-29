"""WS1 Epic 1.2 slice 3: gated --apply-calibration tests.

Isolated: temp DuckDB warehouse, temp active-weights file + versions dir via
env vars. Never touches the repo's bundled scoring_config.yaml or versions.
"""

from __future__ import annotations

import importlib
import json

import duckdb
import pytest
from typer.testing import CliRunner

from job_search_toolkit.cli import app
from job_search_toolkit.pipelines.jd import calibration

runner = CliRunner()

V1 = {  # bundled default (scoring_config.yaml version 1)
    "pay": 0.30,
    "flexibility": 0.25,
    "low_responsibility": 0.20,
    "tech_match": 0.15,
    "company_quality": 0.10,
}


@pytest.fixture()
def tmp_paths(tmp_path, monkeypatch):
    """Redirect active-weights file + versions dir to temp paths."""
    active = tmp_path / "scoring_active.yaml"
    vdir = tmp_path / "scoring_config.versions"
    monkeypatch.setenv("JST_ACTIVE_WEIGHTS_FILE", str(active))
    monkeypatch.setenv("JST_WEIGHTS_VERSIONS_DIR", str(vdir))
    yield {"active": active, "versions": vdir, "tmp": tmp_path}
    # Restore module state: reload score_engine with the env vars gone so the
    # in-process WEIGHTS default (bundled) is what later tests import.
    monkeypatch.delenv("JST_ACTIVE_WEIGHTS_FILE", raising=False)
    monkeypatch.delenv("JST_WEIGHTS_VERSIONS_DIR", raising=False)
    importlib.reload(
        importlib.import_module("job_search_toolkit.pipelines.jd.score_engine")
    )


def _build_warehouse(db_path, jobs):
    """Create a minimal silver.jobs + fact_outcome_event warehouse.

    jobs: list of dicts {job_id, tech_match, applied: bool, advanced: bool}
    (other features default to a neutral 0.5).
    """
    con = duckdb.connect(str(db_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    con.execute(
        "CREATE TABLE silver.jobs (id VARCHAR, scores JSON)"
    )
    con.execute(
        "CREATE TABLE silver.fact_outcome_event "
        "(outcome_event_id VARCHAR, job_id VARCHAR, stage VARCHAR, ts VARCHAR, "
        "note VARCHAR, provenance VARCHAR, recorded_at VARCHAR, synced_at TIMESTAMP)"
    )
    for i, spec in enumerate(jobs):
        scores = {f: 0.5 for f in calibration.FEATURES}
        scores["tech_match"] = spec["tech_match"]
        con.execute(
            "INSERT INTO silver.jobs VALUES (?, ?)",
            [spec["job_id"], json.dumps(scores)],
        )
        if spec["applied"]:
            con.execute(
                "INSERT INTO silver.fact_outcome_event "
                "VALUES (?, ?, 'applied', '2026-01-01T00:00:00Z', NULL, 'test', '2026-01-01T00:00:00Z', NULL)",
                [f"ev-a{i}", spec["job_id"]],
            )
        if spec["advanced"]:
            con.execute(
                "INSERT INTO silver.fact_outcome_event "
                "VALUES (?, ?, 'interview', '2026-02-01T00:00:00Z', NULL, 'test', '2026-02-01T00:00:00Z', NULL)",
                [f"ev-i{i}", spec["job_id"]],
            )
    con.close()


def _clear_signal_jobs():
    """6 high-tech jobs that all advanced; 5 low-tech jobs that all stalled."""
    jobs = [
        {"job_id": f"hi-{i}", "tech_match": 0.9, "applied": True, "advanced": True}
        for i in range(6)
    ]
    jobs += [
        {"job_id": f"lo-{i}", "tech_match": 0.1, "applied": True, "advanced": False}
        for i in range(5)
    ]
    return jobs


def test_apply_calibration_gated(tmp_paths, monkeypatch):
    db = tmp_paths["tmp"] / "jobs.db"
    _build_warehouse(db, _clear_signal_jobs())
    monkeypatch.setattr(
        "job_search_toolkit.pipelines.jd.config.WAREHOUSE_DB", db
    )

    # Without --apply-calibration: report only, no files written.
    r = runner.invoke(app, ["pipeline", "score-report"])
    assert r.exit_code == 0, r.output
    assert "tech_match" in r.output
    assert "dry run" in r.output
    assert not tmp_paths["active"].exists()
    assert not tmp_paths["versions"].exists()
    # Default weights unchanged (no active file -> bundled defaults).
    se = importlib.reload(
        importlib.import_module("job_search_toolkit.pipelines.jd.score_engine")
    )
    assert se.WEIGHTS == V1
    assert se._load_default_weights() == V1

    # With --apply-calibration: gate passes, active file + version written.
    r = runner.invoke(app, ["pipeline", "score-report", "--apply-calibration"])
    assert r.exit_code == 0, r.output
    assert tmp_paths["active"].exists()
    v1_file = tmp_paths["versions"] / "v1.yaml"
    assert v1_file.exists()

    # Active weights reflect the suggestion: tech_match +0.02, renormalized.
    se = importlib.reload(se)
    active = se._load_default_weights()
    raw = {f: V1[f] + (calibration.DELTA if f == "tech_match" else 0.0)
           for f in calibration.FEATURES}
    total = sum(raw.values())
    expected = {f: round(raw[f] / total, 6) for f in calibration.FEATURES}
    assert active == expected

    # Version file records the previous (v1 baseline) weights — restorable.
    ver = calibration.load_version(tmp_paths["versions"], 1)
    assert ver["previous_weights"] == V1
    assert ver["weights"] == expected

    # Restore by loading the v1 version -> original weights.
    restored = ver["previous_weights"]
    assert restored == V1
    assert sum(restored.values()) == pytest.approx(1.0)


def test_calibration_not_enough_data(tmp_paths, monkeypatch):
    db = tmp_paths["tmp"] / "jobs.db"
    # Sparse: only 2 applied events in the high band (< MIN_COUNT).
    _build_warehouse(db, [
        {"job_id": "hi-0", "tech_match": 0.9, "applied": True, "advanced": True},
        {"job_id": "hi-1", "tech_match": 0.9, "applied": True, "advanced": True},
    ])
    monkeypatch.setattr(
        "job_search_toolkit.pipelines.jd.config.WAREHOUSE_DB", db
    )

    r = runner.invoke(app, ["pipeline", "score-report", "--apply-calibration"])
    assert r.exit_code == 1, r.output
    assert "not enough data" in r.output
    assert not tmp_paths["active"].exists()
    assert not tmp_paths["versions"].exists()

    # Weights unchanged.
    se = importlib.reload(
        importlib.import_module("job_search_toolkit.pipelines.jd.score_engine")
    )
    assert se._load_default_weights() == V1
    assert calibration.compute_suggestion(db) is None
