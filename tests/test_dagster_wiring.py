"""Tests for the new board assets' Dagster wiring + board-agnostic gates.

Covers the per-board silver graph:
- one ``silver_<board>`` asset per scrape, each depending only on its own scrape
- ``scored_jobs`` depends on all per-board silver assets
- no composite ``silver_upsert`` remains
- ``--boards`` selection resolves to exactly the chosen boards' scrape+silver
  assets (other boards excluded)
- datasciencejobs stays opt-in (off the default ranking path)

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
from job_search_toolkit.pipelines.jd.assets.merge import SILVER_BOARD_ASSETS
from job_search_toolkit.pipelines.jd.assets.scrape import BOARD_SCRAPE_ASSETS
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

# The 9 active boards on the default ranking path (datasciencejobs excluded).
ACTIVE_BOARDS = {
    "freework": "freework_jobs",
    "hiringcafe": "hiringcafe_jobs",
    **{b: a.key.path[-1] for b, a in NEW_BOARDS.items()},
}


def test_all_assets_include_active_boards():
    names = {a.key.path[-1] for a in ALL_ASSETS}
    expected = set(ACTIVE_BOARDS.values())
    assert expected <= names


def test_per_board_silver_assets_registered():
    names = {a.key.path[-1] for a in ALL_ASSETS}
    for board in ACTIVE_BOARDS:
        assert f"silver_{board}" in names
    # Every active board (and datasciencejobs) has a silver asset built.
    assert set(SILVER_BOARD_ASSETS) == set(BOARD_SCRAPE_ASSETS)
    for board in ACTIVE_BOARDS:
        assert SILVER_BOARD_ASSETS[board].key.path[-1] == f"silver_{board}"


def test_silver_board_depends_only_on_its_own_scrape():
    """Fault isolation at the graph level: silver_X must not depend on any
    other board's scrape, so Y's failure cannot block X's ingest."""
    for board, silver in SILVER_BOARD_ASSETS.items():
        deps = {k.path[-1] for ks in silver.asset_deps.values() for k in ks}
        scrape_name = BOARD_SCRAPE_ASSETS[board].key.path[-1]
        assert deps == {scrape_name}, f"silver_{board} deps={deps}"


def test_no_composite_silver_upsert():
    names = {a.key.path[-1] for a in ALL_ASSETS}
    assert "silver_upsert" not in names
    import job_search_toolkit.pipelines.jd.assets.merge as M

    assert not hasattr(M, "silver_upsert")


def test_scored_jobs_depends_on_all_per_board_silver():
    """scored_jobs consumes every board's silver plus the resume-from-bronze
    silver_ingest asset (so the ingest job can order score/export/gold after
    it); it must not depend on any enrichment asset (ranking is decoupled from
    the LLM pass)."""
    from job_search_toolkit.pipelines.jd.assets.score import scored_jobs

    deps = {k.path[-1] for ks in scored_jobs.asset_deps.values() for k in ks}
    assert deps == {f"silver_{b}" for b in SILVER_BOARD_ASSETS} | {"silver_ingest"}


def test_full_pipeline_job_excludes_enrichment_assets():
    from job_search_toolkit.pipelines.jd.definitions import ENRICH_ASSETS, RANKING_ASSETS

    ranking_names = {a.key.path[-1] for a in RANKING_ASSETS}
    enrich_names = {a.key.path[-1] for a in ENRICH_ASSETS}
    assert ranking_names.isdisjoint(enrich_names)
    assert {"scored_jobs", "ranked_csv"} <= ranking_names
    assert {"silver_freework", "silver_linkedin_jobs"} <= ranking_names
    assert {"translated", "tech_extracted", "vertical_classified",
            "dim_company_enriched"} <= enrich_names


def test_datasciencejobs_is_opt_in_not_default():
    from job_search_toolkit.pipelines.jd.definitions import RANKING_ASSETS

    ranking_names = {a.key.path[-1] for a in RANKING_ASSETS}
    # Not on the default ranking path (long-running/brittle — see ISSUES.md)…
    assert "datasciencejobs_jobs" not in ranking_names
    assert "silver_datasciencejobs" not in ranking_names
    # …but reachable as an explicit `--boards datasciencejobs` opt-in.
    assert "datasciencejobs" in BOARD_SCRAPE_ASSETS
    assert BOARD_SCRAPE_ASSETS["datasciencejobs"].key.path[-1] == "datasciencejobs_jobs"
    assert "datasciencejobs" in SILVER_BOARD_ASSETS


def test_silver_ingest_registered_in_all_assets():
    """silver_ingest (resume-from-bronze recovery) is in the registry."""
    from job_search_toolkit.pipelines.jd.assets.merge import silver_ingest

    names = {a.key.path[-1] for a in ALL_ASSETS}
    assert "silver_ingest" in names
    assert silver_ingest.key.path[-1] == "silver_ingest"


def test_ingest_assets_are_offline_recovery_path():
    """INGEST_ASSETS = silver_ingest + score/export/gold only — never a scrape
    or per-board silver, so `pipeline ingest` recovers bronze offline."""
    from job_search_toolkit.pipelines.jd.definitions import INGEST_ASSETS

    names = {a.key.path[-1] for a in INGEST_ASSETS}
    assert names == {
        "silver_ingest", "scored_jobs", "warehouse_outcomes",
        "ranked_csv", "gold_views",
        "merged_jobs_export", "freework_enriched_export",
        "serve_refresh",
    }


def test_ingest_job_defined():
    """A named `ingest_job` exists selecting exactly the ingest assets."""
    from job_search_toolkit.pipelines.jd.definitions import INGEST_ASSETS, defs

    job = defs.get_job_def("ingest_job")
    sel = job.asset_layer.selected_asset_keys
    assert {k.path[-1] for k in sel} == {a.key.path[-1] for a in INGEST_ASSETS}


def test_silver_ingest_has_ingest_resource():
    """silver_ingest declares the `ingest` resource so the CLI can inject an
    explicit run_id via run_config; the default (no config) is a no-op."""
    from job_search_toolkit.pipelines.jd.assets.merge import silver_ingest

    req = silver_ingest.resource_defs
    assert "ingest" in req


def test_boards_selection_excludes_other_boards():
    """`--boards linkedin_jobs linkedin_posts` must resolve to exactly those
    boards' scrape + silver assets plus downstream — never other boards."""
    from job_search_toolkit.pipelines.jd import run as run_mod
    from job_search_toolkit.pipelines.jd.definitions import defs

    sel = run_mod._boards_selection(["linkedin_jobs", "linkedin_posts"])
    resolved = {k.path[-1] for k in sel.resolve(defs.resolve_asset_graph())}

    expected = {
        "linkedin_jobs", "linkedin_posts",
        "silver_linkedin_jobs", "silver_linkedin_posts",
        "scored_jobs", "ranked_csv", "gold_views",
        "merged_jobs_export", "freework_enriched_export",
    }
    assert expected <= resolved
    # No other board's scrape or silver may be selected.
    excluded = {
        "freework_jobs", "hiringcafe_jobs", "hellowork_jobs", "englishjobs_jobs",
        "faruse_jobs", "wwr_jobs", "remoteok_jobs", "datasciencejobs_jobs",
        "silver_freework", "silver_hiringcafe", "silver_hellowork",
        "silver_englishjobs", "silver_faruse", "silver_wwr", "silver_remoteok",
        "silver_datasciencejobs",
    }
    assert not (resolved & excluded)


def test_boards_selection_datasciencejobs_opt_in():
    """`--boards datasciencejobs` selects its scrape + silver + downstream."""
    from job_search_toolkit.pipelines.jd import run as run_mod
    from job_search_toolkit.pipelines.jd.definitions import defs

    sel = run_mod._boards_selection(["datasciencejobs"])
    resolved = {k.path[-1] for k in sel.resolve(defs.resolve_asset_graph())}
    assert {"datasciencejobs_jobs", "silver_datasciencejobs", "scored_jobs"} <= resolved
    assert not (resolved & {"freework_jobs", "silver_freework"})


def test_boards_selection_single_board_excludes_others_silver():
    from job_search_toolkit.pipelines.jd import run as run_mod
    from job_search_toolkit.pipelines.jd.definitions import defs

    sel = run_mod._boards_selection(["freework"])
    resolved = {k.path[-1] for k in sel.resolve(defs.resolve_asset_graph())}
    assert {"freework_jobs", "silver_freework", "scored_jobs"} <= resolved
    assert "silver_linkedin_jobs" not in resolved
    assert "linkedin_jobs" not in resolved


# ---------------------------------------------------------------------------
# Board-agnostic enrichment gates (unchanged behavior)
# ---------------------------------------------------------------------------

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


def test_dim_company_gate_selects_unenriched_golden_rows(wh):
    """Golden grain: the gate is purely the missing-company_type condition —
    no per-board filter; a row with company_type already derived is excluded."""
    con, _ = wh
    _upsert(con, "run1", [
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
    ])
    con.execute(
        "UPDATE silver.dim_company SET company_type = 'product-led' "
        "WHERE source_board = 'hiringcafe'"
    )
    rows = con.execute(
        f"SELECT source_board FROM silver.dim_company WHERE {S.GOLDEN_DIM_COMPANY_GATE}"
    ).fetchall()
    assert [r[0] for r in rows] == ["englishjobs"]


def test_no_freework_hardcoding_in_gates():
    assert "source_board = 'freework'" not in S.GATE_CLASSIFY
    assert "source_board = 'freework'" not in S.GOLDEN_DIM_COMPANY_GATE


def _upsert(con, run_id: str, jobs: list[dict]):
    S.ensure_dims(con)
    columns = S.ensure_jobs_table(con, jobs)
    S.upsert_run(con, run_id, jobs, columns)


@pytest.fixture
def wh(tmp_path, monkeypatch):
    """A warehouse on a throwaway DB file, with WAREHOUSE_DB pointed at it."""
    db = tmp_path / "jobs.db"
    from job_search_toolkit.pipelines.jd import config
    monkeypatch.setattr(config, "WAREHOUSE_DB", db)
    con = S.connect()
    yield con, db
    con.close()
