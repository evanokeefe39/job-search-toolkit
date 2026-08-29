"""Contract tests for the outcome-loop tracker (WS1, Task A2).

These tests define the FROZEN interface of the ``job_search_toolkit.tracker``
package. The package does not exist yet; until it is implemented these tests
fail (import errors). They are the regression gate for the later phase and the
later implementation MUST match the signatures below.

Contract (from tasks/plans/ws1-outcome-loop.md):

    # src/job_search_toolkit/tracker/protocol.py

    class Tracker(Protocol):
        def record(self, job_id: str, stage: str, ts: str,
                   note: str | None = None) -> None: ...
        def current(self, job_id: str) -> dict | None: ...
        def iter_outcomes(self) -> list[dict]: ...

    Every outcome event dict has AT LEAST the keys:
        {job_id, stage, ts, note, provenance, recorded_at}
    where provenance is "sqlite" or "twenty".

    Stage vocabulary (any other value -> ValueError):
        discovered, shortlisted, researching, tailoring, ready,
        applied, interview, offer, rejected, withdrawn, ghosted

    # src/job_search_toolkit/tracker/__init__.py

    def get_tracker(config: dict | None = None,
                    db_path: Path | None = None) -> Tracker:
        \"\"\"Factory. Reads config["tracker"]["backend"]; default "sqlite".
        Unknown backend -> ValueError listing valid backends.
        db_path overrides the sqlite DB location (default data/tracker.db).
        \"\"\"

    class SQLiteTracker:            # module tracker/sqlite_backend.py
        def __init__(self, db_path: Path) -> None: ...

    class TwentyTracker:            # module tracker/twenty_backend.py
        def __init__(self, crm: CRMCommand | None = None) -> None: ...
        # Reads events via a crm-bridge client; no live network in tests.

Behaviour pinned by these tests:
    - Append-only: record() appends events, never mutates prior ones;
      current() returns the latest event by ts; identical (job_id, stage,
      ts, note) re-records do not double-count.
    - Missing/corrupt DB file is recreated with a warning, never crashes.
    - get_tracker() with no tracker section / backend "sqlite" returns a
      sqlite-backed tracker; unknown backend raises ValueError.
    - The twenty backend produces event dicts with the same shape/keys and
      provenance="twenty" as sqlite produces with provenance="sqlite".

Run: uv run pytest tests/test_tracker.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_search_toolkit import tracker
from job_search_toolkit.tracker import SQLiteTracker, get_tracker

VALID_STAGES = (
    "discovered", "shortlisted", "researching", "tailoring", "ready",
    "applied", "interview", "offer", "rejected", "withdrawn", "ghosted",
)
EVENT_KEYS = {"job_id", "stage", "ts", "note", "provenance", "recorded_at"}


def make_sqlite(tmp_path: Path) -> SQLiteTracker:
    return SQLiteTracker(db_path=tmp_path / "tracker.db")


def test_tracker_sqlite_append_only(tmp_path: Path) -> None:
    t = make_sqlite(tmp_path)
    t.record("j1", "discovered", "2026-08-01T10:00:00+00:00")
    t.record("j1", "applied", "2026-08-05T10:00:00+00:00", note="via freework")
    t.record("j1", "interview", "2026-08-20T10:00:00+00:00")

    events = t.iter_outcomes()
    assert len(events) == 3
    assert [e["stage"] for e in events] == ["discovered", "applied", "interview"]

    # Append-only: re-recording an OLD stage must not mutate prior events.
    t.record("j1", "shortlisted", "2026-08-03T10:00:00+00:00")
    events2 = t.iter_outcomes()
    assert len(events2) == 4
    assert events2[0]["stage"] == "discovered"
    assert events2[0] == events[0]

    # current() returns the latest event by ts (interview, not shortlisted).
    cur = t.current("j1")
    assert cur is not None
    assert cur["stage"] == "interview"
    assert cur["ts"] == "2026-08-20T10:00:00+00:00"
    assert set(cur) >= EVENT_KEYS

    # current() for an unknown job -> None
    assert t.current("nope") is None

    # All events carry the required shape + sqlite provenance.
    for e in events2:
        assert set(e) >= EVENT_KEYS
        assert e["provenance"] == "sqlite"
        assert e["job_id"] == "j1"


def test_tracker_config_validation(tmp_path: Path) -> None:
    # Unknown backend -> ValueError listing the valid backends.
    with pytest.raises(ValueError) as exc:
        get_tracker(config={"tracker": {"backend": "postgres"}})
    msg = str(exc.value)
    assert "sqlite" in msg and "twenty" in msg

    # No tracker section -> sqlite default, no error.
    t = get_tracker(config={}, db_path=tmp_path / "a.db")
    assert isinstance(t, SQLiteTracker)

    # Explicit sqlite -> sqlite.
    t2 = get_tracker(config={"tracker": {"backend": "sqlite"}},
                     db_path=tmp_path / "b.db")
    assert isinstance(t2, SQLiteTracker)


def test_tracker_twenty_identical_protocol(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCRM:
        """Stands in for the crm-bridge client (no live Twenty)."""

        def fetch_outcomes(self) -> list[dict]:
            return [
                {"job_id": "j1", "stage": "applied",
                 "ts": "2026-08-05T10:00:00+00:00", "note": "crm-bridge export"},
                {"job_id": "j1", "stage": "interview",
                 "ts": "2026-08-20T10:00:00+00:00", "note": None},
            ]

    # Patch the CRMCommand symbol the twenty backend resolves at import time.
    monkeypatch.setattr(
        "job_search_toolkit.tracker.twenty_backend.CRMCommand", FakeCRM
    )
    from job_search_toolkit.tracker import TwentyTracker

    twenty = TwentyTracker(crm=FakeCRM())
    sqlite = make_sqlite(tmp_path)
    sqlite.record("j1", "applied", "2026-08-05T10:00:00+00:00",
                  note="crm-bridge export")
    sqlite.record("j1", "interview", "2026-08-20T10:00:00+00:00")

    te, se = twenty.iter_outcomes(), sqlite.iter_outcomes()
    assert len(te) == len(se) == 2
    for a, b in zip(te, se):
        # Same business payload and same key set; only provenance differs.
        assert set(a) == set(b) >= EVENT_KEYS
        for key in ("job_id", "stage", "ts", "note"):
            assert a[key] == b[key]
        assert a["provenance"] == "twenty"
        assert b["provenance"] == "sqlite"

    # Sanity: the factory wires the twenty backend too.
    t20 = get_tracker(config={"tracker": {"backend": "twenty"}}, db_path=tmp_path / "c.db")
    assert not isinstance(t20, SQLiteTracker)
    assert isinstance(t20, tracker.Tracker)


def test_tracker_rejects_unknown_stage(tmp_path: Path) -> None:
    t = make_sqlite(tmp_path)
    for bad in ("hired", "", "APPLIED", "screening"):
        with pytest.raises(ValueError):
            t.record("j1", bad, "2026-08-01T10:00:00+00:00")
    # Nothing was written for rejected stages.
    assert t.iter_outcomes() == []
    # Every valid stage is accepted.
    for stage in VALID_STAGES:
        t.record("j1", stage, "2026-08-02T10:00:00+00:00")
    assert len(t.iter_outcomes()) == len(VALID_STAGES)


def test_tracker_duplicate_idempotent(tmp_path: Path) -> None:
    t = make_sqlite(tmp_path)
    evt = ("j1", "applied", "2026-08-05T10:00:00+00:00", "via freework")
    t.record(*evt)
    t.record(*evt)  # exact duplicate (job_id, stage, ts, note)
    assert len(t.iter_outcomes()) == 1


def test_tracker_missing_db_recreated(tmp_path: Path) -> None:
    # A missing DB path: constructed fresh, record works immediately.
    db = tmp_path / "tracker.db"
    t = SQLiteTracker(db_path=db)
    t.record("j1", "discovered", "2026-08-01T10:00:00+00:00")
    assert len(t.iter_outcomes()) == 1

    # A corrupt DB file is recreated with a warning, never crashes.
    db.write_bytes(b"not a sqlite database at all")
    with pytest.warns(UserWarning):
        t2 = SQLiteTracker(db_path=db)
    t2.record("j1", "applied", "2026-08-05T10:00:00+00:00")
    assert len(t2.iter_outcomes()) == 1

    # A directory where the DB should be is likewise handled.
    with pytest.warns(UserWarning):
        t3 = SQLiteTracker(db_path=tmp_path)
    t3.record("j1", "interview", "2026-08-20T10:00:00+00:00")
    assert len(t3.iter_outcomes()) == 1
