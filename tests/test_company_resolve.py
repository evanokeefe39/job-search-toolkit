"""Behavioral tests for the company golden-record dedup.

Run: uv run python -m pytest tests/test_company_resolve.py -q

Covers the contracts in tasks/plans/company-canonical-dedup.md:
- one golden dim_company row per real company (dedup in place, aliases kept)
- the DECIDED sentiment aggregation rule
- idempotent silver.jobs.company_id re-key backfill
- incremental resolution: separate asset OFF the zero-LLM ranking graph,
  new-names-only (already-resolved names never re-resolved)
- the P=1.00 fuzzy-auto rule set: generic-token subsets never auto-merge,
  fuzzy >=95 auto-accept, board mangling (alstom/astorm) routed to review
"""

from __future__ import annotations

import json

import duckdb
import pytest

from job_search_toolkit.pipelines.jd import silver as S
from job_search_toolkit.pipelines.jd.company_resolve import (
    DEDUP_VERSION,
    aggregate_sentiment,
    apply_review_decision,
    dedup_dim_company,
    fuzzy_auto,
    golden_company_id,
    resolve_new_names,
    review_reason,
)


@pytest.fixture
def con():
    """In-memory warehouse with silver dims + an empty fact table."""
    c = duckdb.connect(":memory:")
    S.ensure_dims(c)
    c.execute(
        "CREATE TABLE silver.jobs (id VARCHAR, source_board VARCHAR, "
        "title VARCHAR, company VARCHAR, company_id VARCHAR, "
        "first_seen_at TIMESTAMP, PRIMARY KEY (id, source_board))"
    )
    yield c
    c.close()


def _add_dim(con, name: str, board: str, **kw) -> str:
    """Insert one per-board dim row (pre-dedup grain) and return its id."""
    cid = S.company_id(name, board)
    cols = ["company_id", "name", "display_name", "source_board"]
    vals = [cid, S.normalize_company_name(name), name, board]
    for k, v in kw.items():
        cols.append(k)
        vals.append(v)
    con.execute(
        f"INSERT INTO silver.dim_company ({', '.join(cols)}) "
        f"VALUES ({', '.join(repr(v) if not isinstance(v, (int, float)) else str(v) for v in vals)})"
    )
    return cid


def _notes(v):
    """news_notes round-trips as a JSON string; normalize for assertions."""
    return json.loads(v) if isinstance(v, str) else (v or [])


def _add_job(con, jid: str, company: str, board: str, cid: str) -> None:
    con.execute(
        "INSERT INTO silver.jobs (id, source_board, title, company, company_id, "
        "first_seen_at) VALUES (?, ?, 'Engineer', ?, ?, NOW())",
        [jid, board, company, cid],
    )


# ---------------------------------------------------------------------------
# (a) one golden row per real company
# ---------------------------------------------------------------------------

def test_dedup_merges_per_board_rows_into_one_golden_row(con):
    hc = _add_dim(con, "doctolib", "hiringcafe", org_type="private",
                  news_sentiment="positive", news_notes=json.dumps(["hiring boom"]))
    ej = _add_dim(con, "doctolib gmbh", "englishjobs",
                  news_sentiment="positive", news_notes=json.dumps(["expands DE"]))
    _add_job(con, "j1", "doctolib", "hiringcafe", hc)
    _add_job(con, "j2", "doctolib gmbh", "englishjobs", ej)

    stats = dedup_dim_company(con)

    rows = con.execute(
        "SELECT company_id, name, dedup_version FROM silver.dim_company"
    ).fetchall()
    assert len(rows) == 1
    gid = golden_company_id("doctolib")
    assert rows[0] == (gid, "doctolib", DEDUP_VERSION)
    # Both board names survive as aliases pointing at the golden id.
    alias = dict(con.execute(
        "SELECT alias_name, company_id FROM silver.company_alias"
    ).fetchall())
    assert alias == {"doctolib": gid, "doctolib gmbh": gid}
    # Enrichment survivorship + notes concatenated + marker written.
    row = con.execute(
        "SELECT org_type, news_sentiment, news_notes FROM silver.dim_company"
    ).fetchone()
    assert row[0] == "private"
    assert set(_notes(row[2])) == {"hiring boom", "expands DE"}
    # Fact rows re-keyed to the golden id.
    assert con.execute(
        "SELECT DISTINCT company_id FROM silver.jobs"
    ).fetchall() == [(gid,)]
    assert stats["merged"] == 1 and stats["rekeyed"] == 2


def test_dedup_never_creates_dim_canonical(con):
    _add_dim(con, "doctolib", "hiringcafe")
    dedup_dim_company(con)
    tables = {
        r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'silver'"
        ).fetchall()
    }
    assert "dim_canonical" not in tables
    assert "company_alias" in tables


# ---------------------------------------------------------------------------
# (b) sentiment aggregation (DECIDED rule)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sentiments,expected",
    [
        (["positive", "positive"], "positive"),
        (["positive", "negative"], "mixed"),
        (["positive", "inconclusive"], "positive"),
        (["inconclusive", "inconclusive"], "inconclusive"),
        (["inconclusive"], "inconclusive"),
        ([], "inconclusive"),
        (["positive", "negative", "inconclusive"], "mixed"),
    ],
)
def test_aggregate_sentiment_rule(sentiments, expected):
    assert aggregate_sentiment(sentiments) == expected


def test_dedup_reaggregates_sentiment_across_group(con):
    _add_dim(con, "doctolib", "hiringcafe", news_sentiment="positive",
             news_notes=json.dumps(["a"]))
    _add_dim(con, "doctolib gmbh", "englishjobs", news_sentiment="negative",
             news_notes=json.dumps(["b"]))
    dedup_dim_company(con)
    sent, notes = con.execute(
        "SELECT news_sentiment, news_notes FROM silver.dim_company"
    ).fetchone()
    assert sent == "mixed"
    assert set(_notes(notes)) == {"a", "b"}


def test_dedup_all_inconclusive_stays_inconclusive(con):
    _add_dim(con, "doctolib", "hiringcafe", news_sentiment="inconclusive",
             news_notes=json.dumps(["x"]))
    _add_dim(con, "doctolib gmbh", "englishjobs", news_sentiment="inconclusive")
    dedup_dim_company(con)
    sent, notes = con.execute(
        "SELECT news_sentiment, news_notes FROM silver.dim_company"
    ).fetchone()
    assert sent == "inconclusive"
    assert _notes(notes) == ["x"]  # note kept, no fabrication


# ---------------------------------------------------------------------------
# (c) re-key backfill idempotent
# ---------------------------------------------------------------------------

def test_dedup_and_rekey_idempotent(con):
    hc = _add_dim(con, "doctolib", "hiringcafe")
    ej = _add_dim(con, "doctolib gmbh", "englishjobs")
    _add_job(con, "j1", "doctolib", "hiringcafe", hc)
    _add_job(con, "j2", "doctolib gmbh", "englishjobs", ej)

    first = dedup_dim_company(con)
    snapshot = con.execute(
        "SELECT id, company_id FROM silver.jobs ORDER BY id"
    ).fetchall()
    dim_snapshot = con.execute(
        "SELECT company_id, name, org_type FROM silver.dim_company ORDER BY name"
    ).fetchall()

    second = dedup_dim_company(con)

    assert first["already"] is False
    assert second["already"] is True
    assert second["merged"] == 0
    assert con.execute(
        "SELECT id, company_id FROM silver.jobs ORDER BY id"
    ).fetchall() == snapshot
    assert con.execute(
        "SELECT company_id, name, org_type FROM silver.dim_company ORDER BY name"
    ).fetchall() == dim_snapshot
    assert con.execute("SELECT COUNT(*) FROM silver.dim_company").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# (d) resolution asset: OFF the ranking graph + new-names-only
# ---------------------------------------------------------------------------

def test_resolution_asset_off_ranking_graph():
    """scored_jobs/ranked_csv must not depend on company_names_resolved."""
    from job_search_toolkit.pipelines.jd.definitions import ALL_ASSETS

    by_key = {a.key.to_user_string(): a for a in ALL_ASSETS}
    assert "company_names_resolved" in by_key
    for ranked in ("scored_jobs", "ranked_csv"):
        assert ranked in by_key
        dep_keys = {k.to_user_string() for k in by_key[ranked].asset_deps}
        assert "company_names_resolved" not in dep_keys, (
            f"{ranked} must stay off the resolution asset (zero-LLM path)"
        )


def test_resolve_new_names_new_only_and_idempotent(con):
    gid = _add_dim(con, "doctolib", "hiringcafe")
    _add_job(con, "j1", "doctolib", "hiringcafe", gid)
    dedup_dim_company(con)  # seeds the alias registry from the dim

    first = resolve_new_names(con)
    assert first["self_seeded"] == 0  # doctolib is already known
    assert con.execute("SELECT COUNT(*) FROM silver.dim_company").fetchone()[0] == 1

    # A new name matching the registry via the stem ladder re-keys, no new row.
    _add_job(con, "j2", "doctolib gmbh", "englishjobs",
             S.company_id("doctolib gmbh", "englishjobs"))
    second = resolve_new_names(con)
    assert second["resolved"] == 1
    assert con.execute(
        "SELECT DISTINCT company_id FROM silver.jobs WHERE id = 'j2'"
    ).fetchone()[0] == golden_company_id("doctolib")
    assert con.execute("SELECT COUNT(*) FROM silver.dim_company").fetchone()[0] == 1

    # Re-run: everything already in the registry — no-ops, never re-resolved.
    third = resolve_new_names(con)
    assert third["resolved"] == 0 and third["self_seeded"] == 0
    assert third["rekeyed"] == 0


def test_resolve_new_names_self_seeds_unknown(con):
    _add_job(con, "j1", "brand new co", "freework",
             S.company_id("brand new co", "freework"))
    stats = resolve_new_names(con)
    assert stats["self_seeded"] == 1
    gid = golden_company_id("brand new co")
    assert con.execute(
        "SELECT company_id FROM silver.jobs WHERE id = 'j1'"
    ).fetchone()[0] == gid
    row = con.execute(
        "SELECT name, dedup_version FROM silver.dim_company WHERE company_id = ?",
        [gid],
    ).fetchone()
    assert row == ("brand new co", DEDUP_VERSION)


# ---------------------------------------------------------------------------
# (e) fuzzy-auto P=1.00 rule set + review queue routing
# ---------------------------------------------------------------------------

def test_fuzzy_auto_token_same_or_concat():
    assert fuzzy_auto("mistral ai", "mistral ai sas") is not None
    assert fuzzy_auto("doctolib", "doctolib gmbh") is not None


def test_fuzzy_auto_token_concat_below_threshold_never_auto():
    # Same token characters, different split, score < 95 -> NOT auto.
    assert fuzzy_auto("qube research & technologies",
                      "quberesearchandtechnologies") is None


def test_generic_token_subset_never_auto_merges():
    # king/one/philips are real company names — a generic or short extra
    # token must never auto-merge, whatever the score.
    for a, b in (("king", "king com"), ("nine", "saas nine")):
        assert fuzzy_auto(a, b) is None, (a, b)
        reason = review_reason(a, b)
        assert reason is not None and reason[1] == "subset-generic-or-short"


def test_fuzzy_auto_subst_extra_above_threshold():
    # Non-generic extra token (len >= 6) with score >= 95 -> auto-merge.
    res = fuzzy_auto("datadog", "datadog robotics")
    assert res is not None and res[0] >= 95.0 and res[1] == "subset-extra"


def test_fuzzy_below_95_same_tokens_is_typo_band_review():
    reason = review_reason("inspiire", "inspire")
    assert reason is not None and reason[0] < 95.0
    assert reason[1] == "typo-band"


def test_board_mangling_routes_to_review():
    # alstom/astorm — same characters, board-mangled, sub-95 score: must be
    # queued for human review, never silently rejected nor auto-merged.
    assert fuzzy_auto("alstom", "astorm") is None
    reason = review_reason("alstom", "astorm")
    assert reason is not None and reason[1] == "board-mangling"


def test_unrelated_names_rejected():
    assert review_reason("doctolib", "ubisoft") is None
    assert fuzzy_auto("doctolib", "ubisoft") is None


def test_review_decision_merge_reaggregates_and_rekeys(con):
    a = _add_dim(con, "alstom", "freework", news_sentiment="positive",
                 news_notes=json.dumps(["n1"]))
    b = _add_dim(con, "astorm", "welcometothejungle", news_sentiment="negative",
                 news_notes=json.dumps(["n2"]))
    _add_job(con, "j1", "astorm", "welcometothejungle", b)
    dedup_dim_company(con)  # both names registered (not auto-clustered)
    resolve_new_names(con)

    out = apply_review_decision(con, "alstom", "astorm", merge=True)

    assert out["merged"] is True
    assert con.execute("SELECT COUNT(*) FROM silver.dim_company").fetchone()[0] == 1
    sent, notes = con.execute(
        "SELECT news_sentiment, news_notes FROM silver.dim_company"
    ).fetchone()
    assert sent == "mixed" and set(_notes(notes)) == {"n1", "n2"}
    # Fact rows re-keyed onto the surviving golden row.
    kept = out["kept"]
    assert con.execute(
        "SELECT DISTINCT company_id FROM silver.jobs"
    ).fetchall() == [(kept,)]
    assert con.execute(
        "SELECT COUNT(*) FROM silver.company_alias "
        "WHERE company_id = '" + kept + "'"
    ).fetchone()[0] == 2


def test_review_decision_reject_is_append_only(con):
    _add_dim(con, "nine", "freework")
    _add_dim(con, "saas nine", "welcometothejungle")
    dedup_dim_company(con)  # both names registered (not auto-clustered)
    resolve_new_names(con)
    out = apply_review_decision(con, "nine", "saas nine", merge=False)
    assert out["merged"] is False and out.get("rejected") is True
    # Still two golden rows — a rejection never merges.
    assert con.execute("SELECT COUNT(*) FROM silver.dim_company").fetchone()[0] == 2
    ids = {r[0] for r in con.execute(
        "SELECT company_id FROM silver.company_alias").fetchall()}
    assert len(ids) == 2


# ---------------------------------------------------------------------------
# (f) reviewer findings: narrowed typo band (F4), mangling floor (F5),
#     deduped rejection writes (F7), self-healing review export (F2/F1)
# ---------------------------------------------------------------------------

def test_typo_band_narrowed_to_single_token_or_equal_multiset():
    # Unrelated multi-token companies in the 85-95 band are noise: never
    # queued (reviewer F4 — '1g link consulting'/'bk consulting' at 87.0).
    assert review_reason("1g link consulting", "bk consulting") is None
    # Single-token misspellings at 85-95 still queue.
    r = review_reason("inspiire", "inspire")
    assert r is not None and 85.0 <= r[0] < 95.0 and r[1] == "typo-band"


def test_board_mangling_anagram_has_score_floor():
    # 'meta'/'team' — same characters, different tokens, score ~40: below
    # the typo band this is coincidence, not board mangling (reviewer F5).
    assert review_reason("meta", "team") is None
    # Real board mangling still queued: alstom/astorm at 83.3 via the 1-2
    # edit branch (single-token, 80-85 band).
    r = review_reason("alstom", "astorm")
    assert r is not None and r[1] == "board-mangling"


def _seed_pair(con, a, b, board_a="freework", board_b="welcometothejungle"):
    import job_search_toolkit.pipelines.jd.company_resolve as cr

    _add_dim(con, a, board_a)
    _add_dim(con, b, board_b)
    dedup_dim_company(con)
    resolve_new_names(con)


def test_review_decision_reject_write_is_deduped(con, tmp_path, monkeypatch):
    # Reviewer F7: repeated rejections of the same pair must not append
    # duplicate lines to the review CSV.
    import job_search_toolkit.pipelines.jd.company_resolve as cr

    out = tmp_path / "review.csv"
    monkeypatch.setattr(cr, "REVIEW_CSV", str(out))
    _seed_pair(con, "nine", "saas nine")
    apply_review_decision(con, "nine", "saas nine", merge=False)
    apply_review_decision(con, "nine", "saas nine", merge=False)
    rows = [line for line in out.read_text(encoding="utf-8").splitlines() if line]
    matches = [r for r in rows if r.split(",")[0:2] in
               (["nine", "saas nine"], ["saas nine", "nine"])]
    assert len(matches) == 1 and matches[0].endswith("rejected")


def test_write_review_queue_self_heals_headerless_history(con, tmp_path):
    # Reviewer F2: the export is a complete artifact — header always present,
    # legacy headerless/duplicated history collapsed, pending pairs listed.
    import job_search_toolkit.pipelines.jd.company_resolve as cr

    out = tmp_path / "review.csv"
    out.write_text(
        "nine,saas nine,,manual,rejected\n"
        "nine,saas nine,,manual,rejected\n",
        encoding="utf-8",
    )
    _seed_pair(con, "mistral", "mistral ai", "englishjobs", "hiringcafe")
    n = cr.write_review_queue(con, str(out))
    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if line]
    assert lines[0] == "name_a,name_b,score,reason,status"
    nine_rows = [line for line in lines if line.startswith("nine,")]
    assert len(nine_rows) == 1 and nine_rows[0].endswith("rejected")
    assert any("mistral,mistral ai" in line and line.endswith("pending")
               for line in lines)
    assert n == len(lines) - 2


def test_write_review_queue_surfaces_spot_check_pairs(con, tmp_path):
    # Reviewer F1: named spot-check pairs are surfaced for human decision
    # even when the narrowed classifier would not queue them (qube variants).
    import job_search_toolkit.pipelines.jd.company_resolve as cr

    out = tmp_path / "review.csv"
    _seed_pair(con, "qube research technologies", "quberesearchandtechnologies")
    assert review_reason("qube research technologies",
                         "quberesearchandtechnologies") is None
    cr.write_review_queue(con, str(out))
    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if line]
    assert any("qube" in line and line.endswith("pending") for line in lines)
