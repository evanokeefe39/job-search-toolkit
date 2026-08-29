"""Contract tests for the follow-up module (WS5 Epic 5.2).

Tests are written against the public contract of
``job_search_toolkit.followup`` and the shared status.yaml schema produced by
``job_search_toolkit.status`` (Epic 5.1). All tests are deterministic: time is
injected via ``now``/``ts``, storage lives in tmp_path, and nothing is sent
anywhere — follow-ups are drafts only.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from job_search_toolkit.followup import (
    FOLLOWUP_THRESHOLD_DAYS,
    MAX_FOLLOWUPS,
    draft_followup,
    followups_due,
)
from job_search_toolkit.status import FollowupCapError
from job_search_toolkit.tracker import STAGES, get_tracker

NOW = datetime(2026, 8, 29, 12, 0, 0)


def _make_app(
    tmp_path: Path,
    slug: str,
    applied_ts: str | None,
    tracker,
    *,
    current_stage: str | None = None,
    followups: int = 0,
) -> Path:
    """Scaffold one application folder with a status.yaml and tracker events.

    ``applied_ts`` — the ts of the ``applied`` transition (also recorded in
    the tracker). ``current_stage`` overrides the tracker's current stage
    (e.g. interview/withdrawn) via an extra event.
    """
    folder = tmp_path / slug
    folder.mkdir(parents=True)
    transitions: list[str] = []
    if applied_ts is not None:
        transitions.append(
            f"  - {{stage: applied, ts: '{applied_ts}', note: submitted}}"
        )
        tracker.record(slug, "applied", applied_ts, note="submitted")
    if current_stage is not None:
        assert current_stage in STAGES
        tracker.record(
            slug, current_stage, "2026-08-25T10:00:00", note="moved on"
        )
    stage = current_stage if current_stage is not None else (
        "applied" if applied_ts is not None else ""
    )
    lines = [
        f"folder: applications/{slug}",
        f"current_stage: {stage}",
        "created_at: '2026-08-01T00:00:00'",
        "transitions:",
        *transitions,
    ]
    if followups:
        lines.append(
            "followups:\n"
            + "\n".join(
                f"  - {{ts: '2026-08-20T09:0{i}:00', note: 'ping {i}'}}"
                for i in range(followups)
            )
        )
    else:
        lines.append("followups: []")
    (folder / "status.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


@pytest.fixture()
def tracker(tmp_path: Path):
    return get_tracker(db_path=tmp_path / "tracker.db")


def test_followup_due_query(tmp_path: Path, tracker) -> None:
    """Only applications still in `applied` past the threshold with room for
    another follow-up appear in the queue, sorted by days_since_applied desc."""
    assert FOLLOWUP_THRESHOLD_DAYS == 10
    assert MAX_FOLLOWUPS == 2

    due = _make_app(
        tmp_path, "2026-08-07_upclear_power-bi-senior-developer",
        "2026-08-07T12:00:00", tracker,
    )
    older = _make_app(
        tmp_path, "2026-08-01_acme_data-engineer", "2026-08-01T12:00:00", tracker
    )
    _make_app(  # replied — current stage interview → excluded
        tmp_path, "2026-08-01_replied_hot_co", "2026-08-01T12:00:00", tracker,
        current_stage="interview",
    )
    _make_app(  # withdrawn → excluded
        tmp_path, "2026-08-01_gone_withdrawn", "2026-08-01T12:00:00", tracker,
        current_stage="withdrawn",
    )
    _make_app(  # too recent → excluded
        tmp_path, "2026-08-25_fresh_new-grad", "2026-08-25T12:00:00", tracker,
    )
    _make_app(  # followup_count >= MAX → excluded
        tmp_path, "2026-08-01_capped_two-pings", "2026-08-01T12:00:00", tracker,
        followups=MAX_FOLLOWUPS,
    )

    queue = followups_due(tmp_path, tracker, now=NOW)

    by_slug = {row["slug"]: row for row in queue}
    assert set(by_slug) == {due.name, older.name}

    row = by_slug[due.name]
    assert row["slug"] == "2026-08-07_upclear_power-bi-senior-developer"
    assert row["folder"] == due
    assert row["company"] == "upclear"
    assert row["role"] == "power-bi-senior-developer"
    assert row["days_since_applied"] == (NOW - datetime(2026, 8, 7, 12, 0, 0)).days
    assert row["followup_count"] == 0

    # deterministic order: days_since_applied descending
    assert by_slug[older.name]["days_since_applied"] > row["days_since_applied"]
    assert [r["slug"] for r in queue] == [older.name, due.name]


def test_followup_draft_only_and_capped(tmp_path: Path, tracker) -> None:
    """Drafting touches ONLY status.yaml (no tracker event, nothing sent) and
    a third follow-up for the same application is refused."""
    folder = _make_app(
        tmp_path, "2026-08-07_upclear_power-bi-senior-developer",
        "2026-08-07T12:00:00", tracker,
    )
    before_events = list(tracker.iter_outcomes())

    updated = draft_followup(folder, "2026-08-20T09:00:00", "just bumping")
    assert updated["followups"] == [{"ts": "2026-08-20T09:00:00", "note": "just bumping"}]
    assert updated["current_stage"] == "applied"

    # second draft is fine (cap is 2)
    draft_followup(folder, "2026-08-27T09:00:00", "second bump")

    # nothing was recorded in the tracker and nothing was sent
    assert list(tracker.iter_outcomes()) == before_events

    # third follow-up is REFUSED
    with pytest.raises(FollowupCapError):
        draft_followup(folder, "2026-08-28T09:00:00", "too many")

    # history is append-only: the two drafts persist, no third row
    yaml_text = (folder / "status.yaml").read_text(encoding="utf-8")
    assert yaml_text.count("- ts: '") == 2
