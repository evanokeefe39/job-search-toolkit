"""Unit tests for the per-source circuit breaker (run isolation).

Covers:
- ``trip_guard`` decorator: a SOURCE-level failure (httpx.HTTPError from
  403/5xx/timeout, or the RuntimeError scrapers raise for bot-block /
  site-structure) trips THIS board for THIS run (recorded in
  ``data/bronze/trips.json``) and returns a normal MaterializeResult instead
  of aborting the whole Dagster run; a successful scrape passes through
  untouched; a genuine code defect (TypeError/KeyError/...) is re-raised to
  fail loudly, never masked as a trip.
- trip-manifest helpers (``append_bronze_trip`` / ``read_run_trips``):
  append-only, isolated by run id, absent-manifest safe.
- ``run._report_trips``: prints tripped sources for a run, no-ops when none
  (including when the materialize result carries no run id).

Run: uv run python -m pytest tests/test_circuit_breaker.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import dagster as dg
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

RUN_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
RUN_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture
def trips(tmp_path, monkeypatch):
    """A throwaway trips manifest, with common.BRONZE_TRIPS pointed at it."""
    from job_search_toolkit.pipelines.jd.assets import common as C

    tfile = tmp_path / "trips.json"
    monkeypatch.setattr(C, "BRONZE_TRIPS", tfile)
    yield tfile


# --- trip-manifest helpers ---------------------------------------------------

def test_append_and_read_trips_isolated_by_run(trips):
    from job_search_toolkit.pipelines.jd.assets.common import (
        append_bronze_trip, read_run_trips,
    )

    append_bronze_trip(RUN_A, "hiringcafe", "Client error '403 Forbidden'")
    append_bronze_trip(RUN_B, "wttj", "Timeout")
    append_bronze_trip(RUN_A, "faruse", "ConnectionError")

    a = read_run_trips(RUN_A)
    b = read_run_trips(RUN_B)
    assert {t["board"] for t in a} == {"hiringcafe", "faruse"}
    assert [t["board"] for t in b] == ["wttj"]
    assert all(t["run_id"] == RUN_A for t in a)
    assert a[0]["error"].startswith("Client error")


def test_read_run_trips_absent_manifest_returns_empty(trips):
    from job_search_toolkit.pipelines.jd.assets.common import read_run_trips

    assert read_run_trips(RUN_A) == []


def test_read_run_trips_other_run_not_included(trips):
    from job_search_toolkit.pipelines.jd.assets.common import (
        append_bronze_trip, read_run_trips,
    )

    append_bronze_trip(RUN_A, "hiringcafe", "403")
    assert read_run_trips(RUN_B) == []


# --- trip_guard decorator ----------------------------------------------------

def test_trip_guard_runtime_error_trips_and_returns_result_not_raise(trips):
    """A source-level RuntimeError (scrapers raise it for bot-block /
    site-structure) is caught: normal MaterializeResult (not a step failure)
    + a trip recorded for the board."""
    from job_search_toolkit.pipelines.jd.assets.scrape import trip_guard
    from job_search_toolkit.pipelines.jd.assets.common import read_run_trips

    def boom(context):
        raise RuntimeError("No buildId in __NEXT_DATA__.")

    ctx = dg.build_asset_context()
    result = trip_guard("hiringcafe")(boom)(ctx)

    assert result is not None
    md = result.metadata
    assert md["tripped"] is True
    assert md["board"] == "hiringcafe"
    assert "No buildId" in str(md["error"])

    recorded = read_run_trips(ctx.run_id)
    assert [t["board"] for t in recorded] == ["hiringcafe"]


def test_trip_guard_http_error_trips(trips):
    """An httpx.HTTPError (403/5xx/timeout escaping raise_for_status) is a
    source failure: caught, recorded, no raise."""
    import httpx
    from job_search_toolkit.pipelines.jd.assets.scrape import trip_guard

    req = httpx.Request("GET", "https://hiringcafe.com/")

    def blocked(context):
        raise httpx.HTTPStatusError(
            "403 Forbidden", request=req, response=httpx.Response(403, request=req)
        )

    ctx = dg.build_asset_context()
    result = trip_guard("hiringcafe")(blocked)(ctx)
    assert result.metadata["tripped"] is True
    assert result.metadata["board"] == "hiringcafe"
    assert "403" in str(result.metadata["error"])


def test_trip_guard_success_not_tripped(trips):
    """A successful scrape returns its result and records no trip."""
    from job_search_toolkit.pipelines.jd.assets.scrape import trip_guard
    from job_search_toolkit.pipelines.jd.assets.common import read_run_trips

    def ok(context):
        return dg.MaterializeResult(metadata={"total": 5})

    ctx = dg.build_asset_context()
    result = trip_guard("freework")(ok)(ctx)

    assert result.metadata.get("total") == 5
    assert "tripped" not in result.metadata
    assert read_run_trips(ctx.run_id) == []


def test_trip_guard_zero_jobs_not_tripped(trips):
    """0 jobs is a legit result, not a trip (only a source failure trips)."""
    from job_search_toolkit.pipelines.jd.assets.scrape import trip_guard
    from job_search_toolkit.pipelines.jd.assets.common import read_run_trips

    def zero(context):
        return dg.MaterializeResult(metadata={"total": 0})

    ctx = dg.build_asset_context()
    result = trip_guard("hellowork")(zero)(ctx)
    assert result.metadata.get("total") == 0
    assert "tripped" not in result.metadata
    assert read_run_trips(ctx.run_id) == []


def test_trip_guard_code_defect_reraises_not_masked(trips):
    """A genuine code defect (TypeError/KeyError/... from scraper or normalize
    logic) must re-raise to fail the run loudly — never be masked as a trip."""
    from job_search_toolkit.pipelines.jd.assets.scrape import trip_guard
    from job_search_toolkit.pipelines.jd.assets.common import read_run_trips

    def buggy(context):
        raise TypeError("can only join an iterable")  # ranked_csv-style regression

    ctx = dg.build_asset_context()
    with pytest.raises(TypeError):
        trip_guard("freework")(buggy)(ctx)
    # the defect must not be recorded as a source trip
    assert read_run_trips(ctx.run_id) == []


def test_all_active_scrape_assets_are_guarded():
    """Every board scrape asset's compute is wrapped by trip_guard."""
    import job_search_toolkit.pipelines.jd.assets.scrape as scrape_mod
    from job_search_toolkit.pipelines.jd.assets.scrape import BOARD_SCRAPE_ASSETS

    src = Path(scrape_mod.__file__).read_text(encoding="utf-8")
    for board in BOARD_SCRAPE_ASSETS:
        assert f'@trip_guard("{board}")' in src, f"{board} not guarded"


# --- run._report_trips -------------------------------------------------------

def test_report_trips_prints_tripped_sources(trips, capsys):
    from job_search_toolkit.pipelines.jd.run import _report_trips
    from job_search_toolkit.pipelines.jd.assets.common import append_bronze_trip

    append_bronze_trip(RUN_A, "hiringcafe", "403 Forbidden")
    _report_trips(RUN_A)
    out = capsys.readouterr().out
    assert "1 source(s) tripped" in out
    assert "hiringcafe" in out


def test_report_trips_noop_when_none(trips, capsys):
    from job_search_toolkit.pipelines.jd.run import _report_trips

    _report_trips(RUN_A)
    assert capsys.readouterr().out == ""


def test_report_trips_none_run_id_noop(trips, capsys):
    """A materialize result without a run id (e.g. a unit-test mock) must not
    crash the end-of-run report."""
    from job_search_toolkit.pipelines.jd.run import _report_trips

    _report_trips(None)
    assert capsys.readouterr().out == ""
