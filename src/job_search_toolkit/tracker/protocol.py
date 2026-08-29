"""Outcome tracker protocol + stage vocabulary (WS1 Epic 1.1).

The tracker is a first-class, append-only outcome event feed. Backends
(SQLite, Twenty CRM) implement the ``Tracker`` protocol; every recorded
event is a dict with at least::

    {job_id, stage, ts, note, provenance, recorded_at}

where ``provenance`` names the backend ("sqlite" or "twenty").
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

#: Canonical stage vocabulary. Any other value is rejected with ValueError.
STAGES: tuple[str, ...] = (
    "discovered",
    "shortlisted",
    "researching",
    "tailoring",
    "ready",
    "applied",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "ghosted",
)


@runtime_checkable
class Tracker(Protocol):
    """Minimal append-only outcome feed interface."""

    def record(self, job_id: str, stage: str, ts: str,
               note: str | None = None) -> None:
        """Append one outcome event; identical events are idempotent."""
        ...

    def current(self, job_id: str) -> dict | None:
        """Latest event for ``job_id`` by ts, or None if unknown."""
        ...

    def iter_outcomes(self) -> list[dict]:
        """All events, in append order."""
        ...
