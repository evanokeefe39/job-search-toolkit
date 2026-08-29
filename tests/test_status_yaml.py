"""Contract tests for WS5 Epic 5.1: application status.yaml + tracker write-through.

The application folder is ``applications/YYYY-MM-DD_<company>_<role>/`` and its
NAME is the tracker ``job_id``. ``status.yaml`` inside the folder holds the
append-only transition history and follow-up drafts; every recorded outcome is
also written to the shared tracker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from job_search_toolkit.status import (
    STATUS_FILE,
    CorruptStatusError,
    FollowupCapError,
    add_followup,
    current_stage,
    followup_count,
    read_status,
    record_outcome,
    write_transition,
)
from job_search_toolkit.tracker import get_tracker


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    """A fresh application folder; status.yaml lives inside it."""
    d = tmp_path / "2026-08-07_upclear_power-bi-senior-developer"
    d.mkdir()
    return d


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "tracker.db"


def _load(folder: Path) -> dict:
    return yaml.safe_load((folder / STATUS_FILE).read_text(encoding="utf-8"))


def test_status_yaml_append_only(folder: Path) -> None:
    """Two transitions -> history has BOTH; an extra record never mutates
    earlier entries (append-only)."""
    write_transition(folder, "shortlisted", "2026-08-06T10:00:00",
                     note="picked from ranked output")
    write_transition(folder, "applied", "2026-08-07T12:00:00")
    status = _load(folder)
    stages = [t["stage"] for t in status["transitions"]]
    assert stages == ["shortlisted", "applied"]

    # An extra (old/repeated) stage record must not mutate earlier entries.
    write_transition(folder, "applied", "2026-08-08T09:00:00")
    status = _load(folder)
    assert [t["stage"] for t in status["transitions"]] == [
        "shortlisted", "applied", "applied",
    ]
    first = status["transitions"][0]
    assert first["stage"] == "shortlisted"
    assert first["ts"] == "2026-08-06T10:00:00"
    assert first["note"] == "picked from ranked output"
    assert status["current_stage"] == "applied"
    assert status["created_at"] == "2026-08-06T10:00:00"
    assert status["folder"] == "applications/2026-08-07_upclear_power-bi-senior-developer"


def test_write_transition_unknown_stage_raises(folder: Path) -> None:
    with pytest.raises(ValueError, match="unknown stage"):
        write_transition(folder, "fired", "2026-08-07T12:00:00")
    # Nothing written on failure.
    assert not (folder / STATUS_FILE).exists()


def test_corrupt_status_yaml_raises_loudly(folder: Path) -> None:
    (folder / STATUS_FILE).write_text(
        "folder: [unclosed\n  transitions: ::broken", encoding="utf-8")
    with pytest.raises(CorruptStatusError) as excinfo:
        read_status(folder)
    # The error must point at the recovery path, never silently overwrite.
    assert str(folder / STATUS_FILE) in str(excinfo.value)
    msg = str(excinfo.value)
    assert "recovery" in msg.lower() or "repair" in msg.lower()
    # write_transition must also refuse to clobber a corrupt file.
    with pytest.raises(CorruptStatusError):
        write_transition(folder, "applied", "2026-08-07T12:00:00")
    assert "folder: [unclosed" in (folder / STATUS_FILE).read_text(encoding="utf-8")


def test_record_outcome_writes_status_and_tracker(
    folder: Path, db_path: Path
) -> None:
    """record_outcome writes BOTH the status.yaml transition AND a tracker
    event keyed on the folder name."""
    out = record_outcome(folder, "applied", "2026-08-07T12:00:00",
                         note="submitted", db_path=db_path)
    assert out["current_stage"] == "applied"
    assert out["transitions"][0]["stage"] == "applied"

    tracker = get_tracker(db_path=db_path)
    event = tracker.current(folder.name)
    assert event is not None
    assert event["job_id"] == folder.name
    assert event["stage"] == "applied"
    assert event["ts"] == "2026-08-07T12:00:00"
    assert event["note"] == "submitted"


def test_record_outcome_idempotent_tracker(
    folder: Path, db_path: Path
) -> None:
    record_outcome(folder, "applied", "2026-08-07T12:00:00", db_path=db_path)
    record_outcome(folder, "applied", "2026-08-07T12:00:00", db_path=db_path)
    tracker = get_tracker(db_path=db_path)
    assert len(tracker.iter_outcomes()) == 1
    assert len(_load(folder)["transitions"]) == 1


def test_followup_cap(folder: Path) -> None:
    assert followup_count(folder) == 0
    add_followup(folder, "2026-08-10T09:00:00", "polite nudge draft")
    assert followup_count(folder) == 1
    add_followup(folder, "2026-08-17T09:00:00", "second nudge draft")
    assert followup_count(folder) == 2
    with pytest.raises(FollowupCapError):
        add_followup(folder, "2026-08-24T09:00:00", "third — over the cap")
    status = _load(folder)
    assert len(status["followups"]) == 2
    assert followup_count(folder) == 2
    # Follow-ups are drafts only: no stage transition, no tracker event.


def test_followups_do_not_touch_transitions(folder: Path, db_path: Path) -> None:
    record_outcome(folder, "applied", "2026-08-07T12:00:00", db_path=db_path)
    add_followup(folder, "2026-08-10T09:00:00", "nudge")
    status = _load(folder)
    assert len(status["transitions"]) == 1
    assert status["current_stage"] == "applied"
    assert status["followups"] == [
        {"ts": "2026-08-10T09:00:00", "note": "nudge"}
    ]


def test_identical_rerecord_does_not_duplicate(folder: Path) -> None:
    """Idempotent append: the exact same (stage, ts, note) twice yields a
    single history entry."""
    write_transition(folder, "shortlisted", "2026-08-06T10:00:00",
                     note="same")
    write_transition(folder, "shortlisted", "2026-08-06T10:00:00",
                     note="same")
    status = _load(folder)
    assert len(status["transitions"]) == 1
    assert status["transitions"][0]["stage"] == "shortlisted"


def test_current_stage_and_read_status_missing(folder: Path) -> None:
    assert read_status(folder) is None
    assert current_stage(folder) is None
    write_transition(folder, "ready", "2026-08-07T12:00:00")
    assert current_stage(folder) == "ready"
