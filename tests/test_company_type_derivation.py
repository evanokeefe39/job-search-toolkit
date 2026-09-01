"""Contract tests for the deterministic ``company_type`` derivation.

``company_type_derived`` (pipelines/jd/assets/enrich.py) is pure SQL over
``silver.dim_company`` + ``silver.jobs`` with first-match-wins precedence:

    esn posting OR consulting/IT-services industry -> it_consulting
    >5000 employees AND tech industry              -> big_tech
    >1000 employees                                -> corporate
    growth funding OR (founded 2008-2017, <=1000)  -> scale_up
    Seed/Series A OR (founded >=2018, <=50)        -> startup
    else                                           -> unknown

These tests pin the precedence ladder, the corrected rule that an
``end_client`` posting alone is NOT a consultancy, and the trusted/derived
interplay (``company_type IS NULL`` gate: a trusted CSV value survives).
"""

from __future__ import annotations

import duckdb
import dagster as dg
import pytest

from job_search_toolkit.pipelines.jd import db as jd_db
from job_search_toolkit.pipelines.jd import silver as S
from job_search_toolkit.pipelines.jd.assets.enrich import company_type_derived


@pytest.fixture()
def wh(tmp_path, monkeypatch):
    """Hermetic warehouse: tmp duckdb file, silver dims, minimal fact table."""
    db = tmp_path / "wh.duckdb"
    monkeypatch.setattr(jd_db, "WAREHOUSE_DB", db)
    con = duckdb.connect(str(db))
    try:
        S.ensure_dims(con)
        # The derivation's EXISTS subquery touches silver.jobs unconditionally.
        con.execute(
            "CREATE TABLE silver.jobs "
            "(company_id VARCHAR, posting_company_type VARCHAR, is_active BOOLEAN)"
        )
    finally:
        con.close()
    return db


def _row(cid: str, **over) -> dict:
    """Minimal dim_company row for _upsert_dim_companies seeding."""
    row = {
        "company_id": cid,
        "name": cid,
        "display_name": cid,
        "source_board": "freework",
    }
    row.update(over)
    return row


def _seed(db, rows: list[dict], jobs: list[tuple[str, str]] = ()) -> None:
    con = duckdb.connect(str(db))
    try:
        S._upsert_dim_companies(con, rows)
        for cid, pct in jobs:
            con.execute("INSERT INTO silver.jobs VALUES (?, ?, TRUE)", [cid, pct])
    finally:
        con.close()


def _derive() -> dg.MaterializeResult:
    return company_type_derived(dg.build_asset_context())


def _company_types(db) -> dict[str, str | None]:
    con = duckdb.connect(str(db))
    try:
        return dict(
            con.execute(
                "SELECT company_id, company_type FROM silver.dim_company"
            ).fetchall()
        )
    finally:
        con.close()


def test_esn_posting_yields_it_consulting(wh):
    """A job routed through an ESN marks the company it_consulting."""
    _seed(wh, [_row("c1")], jobs=[("c1", "esn")])
    _derive()
    assert _company_types(wh)["c1"] == "it_consulting"


def test_end_client_alone_does_not_yield_it_consulting(wh):
    """The corrected rule: 'end_client' is a direct employer, never a
    consultancy — with no other signal the row derives unknown."""
    _seed(wh, [_row("c1")], jobs=[("c1", "end_client")])
    _derive()
    assert _company_types(wh)["c1"] == "unknown"


def test_consulting_industry_yields_it_consulting(wh):
    """A stored IT-services industry token triggers it_consulting without
    any esn posting."""
    _seed(wh, [_row("c1", industry=["IT Services"])])
    _derive()
    assert _company_types(wh)["c1"] == "it_consulting"


def test_big_tech_requires_tech_industry(wh):
    """>5000 employees derives big_tech only in a tech industry; otherwise
    the same size falls through to corporate."""
    _seed(wh, [
        _row("tech", industry=["Software"], size_employees=6000),
        _row("nontech", industry=["Retail"], size_employees=6000),
    ])
    _derive()
    types = _company_types(wh)
    assert types["tech"] == "big_tech"
    assert types["nontech"] == "corporate"


def test_over_1000_employees_yields_corporate(wh):
    _seed(wh, [_row("c1", industry=["Insurance"], size_employees=1001)])
    _derive()
    assert _company_types(wh)["c1"] == "corporate"


def test_growth_funding_yields_scale_up(wh):
    """Series B-I / PE / Debt funding marks scale_up at small size."""
    _seed(wh, [_row("c1", latest_funding_type="Series B", size_employees=120)])
    _derive()
    assert _company_types(wh)["c1"] == "scale_up"


def test_founded_2008_2017_small_yields_scale_up(wh):
    _seed(wh, [_row("c1", year_founded=2012, size_employees=500)])
    _derive()
    assert _company_types(wh)["c1"] == "scale_up"


def test_seed_funding_yields_startup(wh):
    _seed(wh, [_row("c1", latest_funding_type="Seed", size_employees=15)])
    _derive()
    assert _company_types(wh)["c1"] == "startup"


def test_founded_2018_tiny_yields_startup(wh):
    _seed(wh, [_row("c1", year_founded=2019, size_employees=30)])
    _derive()
    assert _company_types(wh)["c1"] == "startup"


def test_no_signal_yields_unknown(wh):
    _seed(wh, [_row("c1", year_founded=1995, size_employees=200)])
    _derive()
    assert _company_types(wh)["c1"] == "unknown"


def test_precedence_consulting_beats_big_tech(wh):
    """First match wins: an esn posting outranks the >5000-employee rule."""
    _seed(
        wh,
        [_row("c1", industry=["Software"], size_employees=6000)],
        jobs=[("c1", "esn")],
    )
    _derive()
    assert _company_types(wh)["c1"] == "it_consulting"


def test_precedence_big_tech_beats_scale_up(wh):
    """>5000 tech outranks growth funding (Series C on a tech giant)."""
    _seed(
        wh,
        [_row("c1", industry=["Cloud"], size_employees=9000,
              latest_funding_type="Series C")],
    )
    _derive()
    assert _company_types(wh)["c1"] == "big_tech"


def test_trusted_company_type_is_not_overwritten(wh):
    """The gate is company_type IS NULL: a trusted CSV-declared value
    survives derivation and is excluded from the derived count."""
    _seed(wh, [
        _row("trusted", company_type="startup", size_employees=6000,
             industry=["Software"]),
        _row("derivable", size_employees=6000, industry=["Software"]),
    ])
    result = _derive()
    types = _company_types(wh)
    assert types["trusted"] == "startup"
    assert types["derivable"] == "big_tech"
    # Only the gated row was processed.
    assert result.metadata["derived"] == 1


def test_derivation_is_deterministic(wh):
    """Re-running with unchanged inputs yields the same values (no LLM)."""
    _seed(wh, [_row("c1", industry=["Software"], size_employees=6000)])
    _derive()
    first = _company_types(wh)["c1"]
    _derive()
    assert _company_types(wh)["c1"] == first == "big_tech"


def test_inactive_esn_posting_does_not_yield_it_consulting(wh):
    """An expired (is_active = FALSE) esn posting no longer pins the
    company to it_consulting."""
    _seed(wh, [_row("c1", year_founded=1995, size_employees=200)])
    con = duckdb.connect(str(wh))
    try:
        con.execute("INSERT INTO silver.jobs VALUES (?, ?, FALSE)", ["c1", "esn"])
    finally:
        con.close()
    _derive()
    assert _company_types(wh)["c1"] == "unknown"


def test_funding_variant_strings_match(wh):
    """Funding variants like 'Series B - Equity' still derive scale_up."""
    _seed(wh, [_row("c1", latest_funding_type="Series B - Equity",
                    size_employees=100)])
    _derive()
    assert _company_types(wh)["c1"] == "scale_up"
