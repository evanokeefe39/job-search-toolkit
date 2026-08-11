"""Tests for the six new board assets' Dagster wiring + board-agnostic gates.

Run: uv run python -m pytest tests/test_dagster_wiring.py -q
"""

from __future__ import annotations

import pytest

from job_search_toolkit.pipelines.jd.assets import (
    datasciencejobs_jobs,
    englishjobs_jobs,
    faruse_jobs,
    hellowork_jobs,
    remoteok_jobs,
    wwr_jobs,
)
from job_search_toolkit.pipelines.jd.assets.merge import silver_upsert
from job_search_toolkit.pipelines.jd.definitions import ALL_ASSETS
from job_search_toolkit.pipelines.jd import silver as S

NEW_BOARDS = {
    "hellowork": hellowork_jobs,
    "englishjobs": englishjobs_jobs,
    "faruse": faruse_jobs,
    "wwr": wwr_jobs,
    "remoteok": remoteok_jobs,
    "datasciencejobs": datasciencejobs_jobs,
}

# dagster exposes upstream deps as `asset_deps`: {AssetKey: set[AssetKey]}.
SILVER_DEPS = {
    k.path[-1] for ks in silver_upsert.asset_deps.values() for k in ks
}

def test_all_assets_include_eight_boards():
    names = {a.key.path[-1] for a in ALL_ASSETS}
    for board in list(NEW_BOARDS) + ["freework", "hiringcafe"]:
        assert f"{board}_jobs" in names


def test_silver_upsert_depends_on_all_eight_boards():
    for board in list(NEW_BOARDS) + ["freework", "hiringcafe"]:
        assert f"{board}_jobs" in SILVER_DEPS


def test_gate_classify_selects_new_boards_excludes_hiringcafe(wh):
    con, _ = wh
    jobs = [
        {
            "id": "h1", "source_board": "hellowork", "title": "Data Engineer",
            "description_text": "text", "description_language": "fr",
            "engagement_type": None,
        },
        {
            "id": "hc1", "source_board": "hiringcafe", "title": "Data Engineer",
            "description_text": "text", "description_language": "en",
            "engagement_type": "direct",
        },
    ]
    _upsert(con, "run1", jobs)
    rows = S.fetch_jobs(con, ["id"], S.GATE_CLASSIFY)
    ids = {r["id"] for r in rows}
    assert ids == {"h1"}


def test_gate_company_selects_new_boards_excludes_hiringcafe(wh):
    con, _ = wh
    jobs = [
        {
            "id": "e1", "source_board": "englishjobs", "title": "Data Analyst",
            "description_text": "text", "description_language": "en",
            "company_info": {"name": "Acme", "org_type": None},
        },
        {
            "id": "hc1", "source_board": "hiringcafe", "title": "Data Analyst",
            "description_text": "text", "description_language": "en",
            "company_info": {"name": "Hc", "org_type": "private"},
        },
    ]
    _upsert(con, "run1", jobs)
    rows = S.fetch_jobs(con, ["id"], S.GATE_COMPANY)
    ids = {r["id"] for r in rows}
    assert ids == {"e1"}


def test_no_freework_hardcoding_in_gates():
    assert "source_board = 'freework'" not in S.GATE_CLASSIFY
    assert "source_board = 'freework'" not in S.GATE_COMPANY


def _upsert(con, run_id: str, jobs: list[dict]):
    columns = S.ensure_jobs_table(con, jobs)
    S.upsert_run(con, run_id, jobs, columns)


@pytest.fixture
def wh(tmp_path, monkeypatch):
    """A warehouse on a throwaway DB file, with WAREHOUSE_DB pointed at it."""
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(S, "WAREHOUSE_DB", db)
    con = S.connect()
    yield con, db
    con.close()
