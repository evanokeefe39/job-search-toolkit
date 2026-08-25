"""Tests for the six new board assets' Dagster wiring + board-agnostic gates.

Run: uv run python -m pytest tests/test_dagster_wiring.py -q
"""

from __future__ import annotations

import pytest

from job_search_toolkit.pipelines.jd.assets import (
    englishjobs_jobs,
    faruse_jobs,
    hellowork_jobs,
    linkedin_jobs,
    linkedin_posts,
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
    "linkedin_jobs": linkedin_jobs,
    "linkedin_posts": linkedin_posts,
}

# dagster exposes upstream deps as `asset_deps`: {AssetKey: set[AssetKey]}.
SILVER_DEPS = {
    k.path[-1] for ks in silver_upsert.asset_deps.values() for k in ks
}

def test_all_assets_include_active_boards():
    names = {a.key.path[-1] for a in ALL_ASSETS}
    expected = {a.key.path[-1] for a in NEW_BOARDS.values()} | {
        "freework_jobs", "hiringcafe_jobs"
    }
    assert expected <= names


def test_silver_upsert_depends_on_all_active_boards():
    expected = {a.key.path[-1] for a in NEW_BOARDS.values()} | {
        "freework_jobs", "hiringcafe_jobs"
    }
    assert expected <= SILVER_DEPS


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


def test_dim_company_gate_selects_new_boards_excludes_hiringcafe(wh):
    con, _ = wh
    jobs = [
        {
            "id": "e1", "source_board": "englishjobs", "title": "Data Analyst",
            "description_text": "text", "description_language": "en",
            "company": "Acme UK",
            "company_info": {"name": "Acme UK", "org_type": None},
        },
        {
            "id": "hc1", "source_board": "hiringcafe", "title": "Data Analyst",
            "description_text": "text", "description_language": "en",
            "company": "Hc",
            "company_info": {"name": "Hc", "org_type": "private"},
        },
    ]
    _upsert(con, "run1", jobs)
    rows = con.execute(
        f"SELECT source_board FROM silver.dim_company WHERE {S.DIM_COMPANY_GATE}"
    ).fetchall()
    assert [r[0] for r in rows] == ["englishjobs"]


def test_no_freework_hardcoding_in_gates():
    assert "source_board = 'freework'" not in S.GATE_CLASSIFY
    assert "source_board = 'freework'" not in S.DIM_COMPANY_GATE


def _upsert(con, run_id: str, jobs: list[dict]):
    S.ensure_dims(con)
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


def test_scored_jobs_depends_only_on_silver_upsert():
    """Ranking is decoupled from LLM enrichment — scored_jobs must not depend
    on any enrichment asset."""
    from job_search_toolkit.pipelines.jd.assets.score import scored_jobs

    deps = {k.path[-1] for ks in scored_jobs.asset_deps.values() for k in ks}
    assert deps == {"silver_upsert"}


def test_full_pipeline_job_excludes_enrichment_assets():
    from job_search_toolkit.pipelines.jd.definitions import ENRICH_ASSETS, RANKING_ASSETS

    ranking_names = {a.key.path[-1] for a in RANKING_ASSETS}
    enrich_names = {a.key.path[-1] for a in ENRICH_ASSETS}
    assert ranking_names.isdisjoint(enrich_names)
    assert {"scored_jobs", "ranked_csv", "silver_upsert"} <= ranking_names
    assert {"translated", "tech_extracted", "vertical_classified",
            "dim_company_enriched"} <= enrich_names


def test_datasciencejobs_is_opt_in_not_default():
    from job_search_toolkit.pipelines.jd.definitions import (
        BOARD_SCRAPE_ASSETS,
        RANKING_ASSETS,
    )

    ranking_names = {a.key.path[-1] for a in RANKING_ASSETS}
    # Not on the default ranking path (long-running/brittle — see ISSUES.md)…
    assert "datasciencejobs_jobs" not in ranking_names
    # …but reachable as an explicit `--boards datasciencejobs` opt-in.
    assert "datasciencejobs" in BOARD_SCRAPE_ASSETS
    assert BOARD_SCRAPE_ASSETS["datasciencejobs"].key.path[-1] == "datasciencejobs_jobs"
