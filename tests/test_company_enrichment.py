"""Contract tests for the CSV company-enrichment source.

``company_enrichment_ingested`` (pipelines/jd/assets/company_enrichment.py)
merges ``company_findings.csv`` into ``silver.dim_company``. The CSV header
is the contract — deviation fails loudly. Merge semantics: COALESCE upsert
(non-NULL CSV inputs win), sources union-deduped into ``company_sources``,
and trusted ``company_type`` values written directly while invalid ones
leave NULL for ``company_type_derived``.
"""

from __future__ import annotations

import csv
import json

import duckdb
import dagster as dg
import pytest

from job_search_toolkit.pipelines.jd import db as jd_db
from job_search_toolkit.pipelines.jd import silver as S
from job_search_toolkit.pipelines.jd.assets.company_enrichment import (
    CSV_COLUMNS,
    CompanyEnrichmentConfig,
    _resolve_company_id,
    company_enrichment_ingested,
)
from job_search_toolkit.pipelines.jd.company_resolve import register_alias


@pytest.fixture()
def wh(tmp_path, monkeypatch):
    """Hermetic warehouse: tmp duckdb file, silver dims, minimal fact table
    (the derivation sibling queries silver.jobs unconditionally)."""
    db = tmp_path / "wh.duckdb"
    monkeypatch.setattr(jd_db, "WAREHOUSE_DB", db)
    con = duckdb.connect(str(db))
    try:
        S.ensure_dims(con)
        con.execute(
            "CREATE TABLE silver.jobs "
            "(company_id VARCHAR, posting_company_type VARCHAR, is_active BOOLEAN)"
        )
    finally:
        con.close()
    return db


def _write_csv(tmp_path, rows: list[list[str]], header=None, name="findings.csv") -> str:
    p = tmp_path / name
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header if header is not None else CSV_COLUMNS)
        w.writerows(rows)
    return str(p)


def _ingest(csv_path: str) -> dg.MaterializeResult:
    return company_enrichment_ingested(
        dg.build_asset_context(), CompanyEnrichmentConfig(csv_path=csv_path)
    )


def _dim(con, cid: str) -> tuple:
    return con.execute(
        "SELECT name, industry, size_employees, year_founded, hq_country, "
        "latest_funding_type, latest_funding_amount_usd, company_type, "
        "company_sources FROM silver.dim_company WHERE company_id = ?",
        [cid],
    ).fetchone()


def _connect(db) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db))


def _cid(name: str, board: str) -> str:
    return S.company_id(name, board)


def test_header_deviation_fails_loudly(wh, tmp_path):
    """Any header drift (missing / reordered / extra column) raises
    ValueError — the CSV shape IS the contract."""
    bad = CSV_COLUMNS[:-1]  # dropped the trailing 'sources' column
    p = _write_csv(
        tmp_path,
        [["Acme", "wttj", "Software", "500", "", "FR", "Seed", "", "", "ref"]],
        header=bad,
    )
    with pytest.raises(ValueError, match="contract"):
        _ingest(p)


def test_contract_header_constant_matches_documented_columns():
    """The module's CSV_COLUMNS is the enforced header; pin its exact shape
    so an accidental reorder cannot slip past silently."""
    assert CSV_COLUMNS == [
        "company_name", "source_board", "industry", "size_employees",
        "year_founded", "hq_country", "latest_funding_type",
        "latest_funding_amount_usd", "company_type", "sources",
    ]


def test_row_parse_industry_ints_and_sources(wh, tmp_path):
    """Merge semantics: JSON-array industry parsed, bare token becomes a
    1-list, optional ints parse (empty -> NULL), sources semicolon-split
    and deduped, metadata reports the row count and source path."""
    p = _write_csv(tmp_path, [
        ["Acme Corp", "wttj", '["Software", "Cloud"]', "500", "", "FR",
         "Series B", "1200000000", "", "https://tavily.com/a; https://exa.com/b;"
         "https://tavily.com/a"],
        ["Beta Consulting", "", "Consulting", "50", "2020", "", "", "",
         "", "https://exa.com/c"],
    ])
    result = _ingest(p)
    assert result.metadata["rows"] == 2
    assert result.metadata["trusted_company_type"] == 0
    assert result.metadata["source"] == p

    con = _connect(wh)
    try:
        name, industry, size, founded, hq, funding, amount, ctype, sources = (
            _dim(con, _cid("Acme Corp", "wttj"))
        )
        assert name == "acme corp"
        assert json.loads(industry) == ["Software", "Cloud"]
        assert (size, founded, hq) == (500, None, "FR")
        assert (funding, amount) == ("Series B", 1_200_000_000)
        assert ctype is None
        assert json.loads(sources) == [
            "https://tavily.com/a", "https://exa.com/b",
        ]
        _, industry2, *_rest = _dim(con, _cid("Beta Consulting", "company_findings"))
        assert json.loads(industry2) == ["Consulting"]
    finally:
        con.close()


def test_trusted_company_type_is_written_and_counted(wh, tmp_path):
    """A valid non-empty company_type in the CSV is accepted as TRUSTED:
    stored verbatim and counted in the trusted_company_type metadata."""
    p = _write_csv(tmp_path, [
        ["Acme", "wttj", "Software", "500", "", "FR", "", "", "startup", "ref-1"],
    ])
    result = _ingest(p)
    assert result.metadata["trusted_company_type"] == 1
    _ingest(p)
    con = _connect(wh)
    try:
        row = _dim(con, _cid("Acme", "wttj"))
        assert row[7] == "startup"
    finally:
        con.close()


def test_invalid_company_type_treated_as_empty(wh, tmp_path):
    """An invalid declared value fails the gate: stored as NULL (available
    for derivation), not trusted."""
    p = _write_csv(tmp_path, [
        ["Acme", "wttj", "Software", "500", "", "FR", "", "", "unicorn", "ref-1"],
    ])
    result = _ingest(p)
    assert result.metadata["trusted_company_type"] == 0
    con = _connect(wh)
    try:
        assert _dim(con, _cid("Acme", "wttj"))[7] is None
    finally:
        con.close()


def test_sources_append_dedup_and_idempotent_rerun(wh, tmp_path):
    """Sources union with stored provenance (dedup, append-only) and re-run
    of the same CSV is idempotent."""
    cid = _cid("Acme", "wttj")
    con = _connect(wh)
    try:
        S._upsert_dim_companies(con, [{
            "company_id": cid, "name": "acme", "display_name": "Acme",
            "source_board": "wttj", "company_sources": ["old.com"],
        }])
    finally:
        con.close()
    p = _write_csv(tmp_path, [
        ["Acme", "wttj", "Software", "500", "", "FR", "", "", "", "new.com;old.com"],
    ])
    _ingest(p)
    _ingest(p)  # idempotent re-run
    con = _connect(wh)
    try:
        assert json.loads(_dim(con, cid)[8]) == ["old.com", "new.com"]
    finally:
        con.close()


def test_absent_csv_noops_without_raising(wh, tmp_path, monkeypatch):
    """A missing CSV is a legitimate no-op in the default newest-CSV-in-dir
    mode: rows=0, source=absent, warehouse untouched. (An explicit missing
    csv_path is a contract error and raises instead.)"""
    from job_search_toolkit.pipelines.jd.assets import company_enrichment as ce

    monkeypatch.setattr(ce, "ENRICHMENT_DIR", tmp_path / "empty_dir")
    result = ce.company_enrichment_ingested(
        dg.build_asset_context(), CompanyEnrichmentConfig(csv_path=None)
    )
    assert result.metadata["source"] == "absent"
    con = _connect(wh)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM silver.dim_company"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_resolve_company_id_prefers_source_board_row(wh):
    """When the (name, source_board) dim row exists it is the merge target,
    even if a golden id is also known."""
    cid = _cid("Acme", "wttj")
    con = _connect(wh)
    try:
        S._upsert_dim_companies(con, [{
            "company_id": cid, "name": "acme", "display_name": "Acme",
            "source_board": "wttj",
        }])
        golden = "golden0123456789"
        assert _resolve_company_id(con, "Acme", "wttj", golden) == cid
    finally:
        con.close()


def test_resolve_company_id_uses_golden_when_no_board_row(wh):
    """Registry-known golden id resolves the row when no per-board row
    matches."""
    con = _connect(wh)
    try:
        register_alias(con, "Acme Corp", "golden0123456789", "dedup")
        assert (
            _resolve_company_id(con, "Acme Corp", "wttj", "golden0123456789")
            == "golden0123456789"
        )
    finally:
        con.close()


def test_resolve_company_id_falls_back_to_normalized_name(wh):
    """No board hit and no golden: any dim row carrying the normalized name
    is the merge target (cross-board match)."""
    existing = _cid("Acme Systems", "hellowork")
    con = _connect(wh)
    try:
        S._upsert_dim_companies(con, [{
            "company_id": existing, "name": "acme systems",
            "display_name": "Acme Systems", "source_board": "hellowork",
        }])
        assert (
            _resolve_company_id(con, "Acme Systems", "freework", None)
            == existing
        )
    finally:
        con.close()


def test_resolve_company_id_new_row_key(wh):
    """Unknown company: registry golden id when known, else a per-board key
    (with a CSV marker board when no board given)."""
    con = _connect(wh)
    try:
        register_alias(con, "Known Co", "golden0123456789", "dedup")
        assert _resolve_company_id(con, "Known Co", None, "golden0123456789") \
            == "golden0123456789"
        assert _resolve_company_id(con, "Mystery Co", None, None) == _cid(
            "Mystery Co", "company_findings"
        )
        assert _resolve_company_id(con, "Mystery Co", "wttj", None) == _cid(
            "Mystery Co", "wttj"
        )
    finally:
        con.close()


def test_trusted_type_survives_derivation_but_empty_does_not(wh, tmp_path):
    """End-to-end trusted/derived precedence: the trusted CSV value is kept
    by company_type_derived (NULL gate) while the empty-type row derives."""
    p = _write_csv(tmp_path, [
        ["Trusted Co", "wttj", "Software", "6000", "", "FR", "", "",
         "startup", "ref-1"],
        ["Fresh Co", "wttj", "Software", "6000", "", "FR", "", "", "", "ref-2"],
    ])
    _ingest(p)
    from job_search_toolkit.pipelines.jd.assets.enrich import (
        company_type_derived,
    )

    company_type_derived(dg.build_asset_context())
    con = _connect(wh)
    try:
        assert _dim(con, _cid("Trusted Co", "wttj"))[7] == "startup"
        assert _dim(con, _cid("Fresh Co", "wttj"))[7] == "big_tech"
    finally:
        con.close()


def test_resolve_company_id_name_fallback_matches_dim_storage(wh):
    """The name-fallback matches dim_company.name storage
    (normalize_company_name, accent-preserving): an accented CSV name finds
    the board-seeded dim row instead of creating a duplicate."""
    existing = _cid("Société Générale", "wttj")
    con = _connect(wh)
    try:
        S._upsert_dim_companies(con, [{
            "company_id": existing, "name": "société générale",
            "display_name": "Société Générale", "source_board": "wttj",
        }])
        assert (
            _resolve_company_id(con, "Société Générale", "freework", None)
            == existing
        )
    finally:
        con.close()


def test_resolve_company_id_golden_row_beats_board_row(wh):
    """Docstring precedence: when the golden id resolves to an existing dim
    row it wins over the per-board row (board rows are invisible to gold
    views joining on the golden id)."""
    board_cid = _cid("Acme", "wttj")
    con = _connect(wh)
    try:
        S._upsert_dim_companies(con, [
            {"company_id": board_cid, "name": "acme",
             "display_name": "Acme", "source_board": "wttj"},
            {"company_id": "golden0123456789", "name": "acme",
             "display_name": "Acme", "source_board": "linkedin"},
        ])
        assert (
            _resolve_company_id(con, "Acme", "wttj", "golden0123456789")
            == "golden0123456789"
        )
    finally:
        con.close()


def test_missing_explicit_csv_path_raises(wh):
    """An explicit csv_path that does not exist is a contract error and
    raises (only the newest-CSV-in-dir mode no-ops silently)."""
    with pytest.raises(FileNotFoundError):
        _ingest(str(wh.parent / "does_not_exist.csv"))


def test_stale_derived_type_resets_on_csv_signal_update(wh, tmp_path):
    """Re-derivation: a CSV update that supplies new signal inputs (no
    trusted company_type) clears the stale derived value so
    company_type_derived re-derives; a trusted value is never cleared."""
    p1 = _write_csv(tmp_path, [
        ["Stale Co", "wttj", "", "", "", "FR", "", "", "", "ref-1"],
        ["Trusted Co", "wttj", "", "", "", "FR", "", "", "startup", "ref-2"],
    ], name="round1.csv")
    _ingest(p1)
    con = _connect(wh)
    try:
        for cid in (_cid("Stale Co", "wttj"), _cid("Trusted Co", "wttj")):
            con.execute(
                "UPDATE silver.dim_company SET company_type = 'unknown' "
                "WHERE company_id = ?", [cid]
            )
    finally:
        con.close()

    p2 = _write_csv(tmp_path, [
        ["Stale Co", "wttj", "Software", "6000", "", "FR", "", "", "", "ref-1"],
        ["Trusted Co", "wttj", "Software", "6000", "", "FR", "", "", "startup",
         "ref-2"],
    ], name="round2.csv")
    _ingest(p2)
    con = _connect(wh)
    try:
        assert _dim(con, _cid("Stale Co", "wttj"))[7] is None
        assert _dim(con, _cid("Trusted Co", "wttj"))[7] == "startup"
    finally:
        con.close()