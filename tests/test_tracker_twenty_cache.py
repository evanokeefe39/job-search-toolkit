"""Tests for TwentyTracker's SQLite sync-cache mirror (WS1 Epic 1.3)."""

from __future__ import annotations

import json

import pytest

from job_search_toolkit.tracker import get_tracker
from job_search_toolkit.tracker.sqlite_backend import SQLiteTracker
from job_search_toolkit.tracker.twenty_backend import CRMCommand, TwentyTracker


class FakeCRM(CRMCommand):
    """Records sync payloads, serves canned stats — no subprocess."""

    def __init__(self, stats: list[dict] | None = None) -> None:
        self.sync_payloads: list[dict] = []
        self.stats = stats or []

    def run(self, *args: str) -> str:
        if args and args[0] == "sync":
            self.sync_payloads.append(json.loads(args[2]))
            return ""
        return json.dumps(self.stats)


def test_twenty_record_mirrors_to_sqlite_cache(tmp_path):
    crm = FakeCRM()
    cache = tmp_path / "tracker.db"
    t = TwentyTracker(crm=crm, cache_path=cache)
    t.record("job-1", "applied", "2026-08-29T10:00:00+00:00", "vial")

    # The authoritative sync reached the crm-bridge...
    assert crm.sync_payloads == [{
        "job_id": "job-1", "stage": "applied",
        "ts": "2026-08-29T10:00:00+00:00", "note": "vial",
    }]
    # ...and the event is mirrored in the SQLite cache
    events = SQLiteTracker(cache).iter_outcomes()
    assert len(events) == 1
    ev = events[0]
    assert (ev["job_id"], ev["stage"], ev["ts"], ev["note"]) == (
        "job-1", "applied", "2026-08-29T10:00:00+00:00", "vial")
    assert ev["provenance"] == "twenty"


def test_twenty_record_mirrors_exact_event(tmp_path):
    crm = FakeCRM()
    cache = tmp_path / "tracker.db"
    TwentyTracker(crm=crm, cache_path=cache).record(
        "job-2", "offer", "2026-08-29T11:00:00+00:00", None)
    events = SQLiteTracker(cache).iter_outcomes()
    assert len(events) == 1
    ev = events[0]
    assert (ev["job_id"], ev["stage"], ev["ts"], ev["note"]) == (
        "job-2", "offer", "2026-08-29T11:00:00+00:00", None)
    assert ev["provenance"] == "twenty"


def test_twenty_cache_mirror_no_fork(tmp_path):
    # Cache holds an extra event Twenty does not know about — the CRM
    # read path must remain authoritative and never leak the cache row.
    crm = FakeCRM(stats=[{"job_id": "job-a", "stage": "applied",
                          "ts": "2026-08-29T09:00:00+00:00"}])
    cache = tmp_path / "tracker.db"
    t = TwentyTracker(crm=crm, cache_path=cache)
    SQLiteTracker(cache).record("ghost", "applied",
                                "2026-08-29T08:00:00+00:00", None)

    events = t.iter_outcomes()
    assert [e["job_id"] for e in events] == ["job-a"]
    assert all(e["provenance"] == "twenty" for e in events)
    assert t.current("ghost") is None


def test_twenty_cache_mirror_failure_is_best_effort(tmp_path):
    crm = FakeCRM()
    # A non-empty directory squatting on the cache path makes the
    # SQLiteTracker mirror raise — the sync must still succeed.
    cache = tmp_path / "tracker.db"
    cache.mkdir()
    (cache / "junk").write_text("x")
    t = TwentyTracker(crm=crm, cache_path=cache)
    with pytest.warns(UserWarning, match="cache mirror"):
        t.record("job-3", "applied", "2026-08-29T12:00:00+00:00", None)
    assert len(crm.sync_payloads) == 1


def test_get_tracker_twenty_no_side_effect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = get_tracker(config={"tracker": {"backend": "twenty"}})
    assert isinstance(t, TwentyTracker)
    assert not (tmp_path / "data/tracker.db").exists()
