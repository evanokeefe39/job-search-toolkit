"""Twenty CRM outcome-tracker backend (WS1 Epic 1.1).

A thin client (:class:`CRMCommand`) shells out to ``uv --directory ../crm
run crm-bridge`` (see skills/application-tracker/SKILL.md).  It is a
protocol adapter only: the pipeline never talks to Twenty directly and
never touches the network unless explicitly asked.

:class:`TwentyTracker` maps the crm-bridge event list onto the same event
dict shape the SQLite backend produces — identical keys, only
``provenance`` differs ("twenty") — so the two backends are drop-in
interchangeable.

T2 sync-cache semantics (WS1 Epic 1.3): at ``tracker.backend = twenty``
Twenty is authoritative and the local SQLite store (default
``data/tracker.db``) mirrors it. ``record()`` syncs to the crm-bridge
first, then appends the event to the cache (best-effort: a cache
failure is warned about, never fails the authoritative write).
``iter_outcomes()`` reads only from the CRM — the cache is a mirror,
never a fork.
"""

from __future__ import annotations

import json
import subprocess
import warnings
from datetime import UTC, datetime
from pathlib import Path

from job_search_toolkit.tracker.protocol import STAGES, Tracker
from job_search_toolkit.tracker.sqlite_backend import SQLiteTracker


class CRMCommand:
    """Shell out to the ``crm-bridge`` CLI in the sibling ``../crm`` repo."""

    def __init__(self, directory: str = "../crm") -> None:
        self.directory = directory

    def _base(self) -> list[str]:
        return ["uv", "--directory", self.directory, "run", "crm-bridge"]

    def run(self, *args: str) -> str:
        """Run ``crm-bridge <args>`` and return stdout."""
        return subprocess.run(
            [*self._base(), *args],
            capture_output=True, text=True, check=True,
        ).stdout

    def fetch_outcomes(self) -> list[dict]:
        """Read the CRM funnel as a list of outcome event dicts.

        The canonical read command is ``crm-bridge stats --json``; keys map
        1:1 onto the event shape (job_id/stage/ts/note per the skill's
        data model). Rows missing required keys are skipped.
        """
        raw = self.run("stats", "--json")
        events: list[dict] = []
        for row in json.loads(raw) if raw.strip() else []:
            if not isinstance(row, dict):
                continue
            if not ({"job_id", "stage", "ts"} <= set(row)):
                continue
            events.append({
                "job_id": row["job_id"],
                "stage": row["stage"],
                "ts": row["ts"],
                "note": row.get("note"),
            })
        return events


class TwentyTracker:
    """Tracker facade over the Twenty CRM via crm-bridge (read path),
    with a best-effort local SQLite mirror of recorded events."""

    def __init__(self, crm: CRMCommand | None = None,
                 cache_path: Path | None = None) -> None:
        self.crm = crm if crm is not None else CRMCommand()
        # Default mirror location; the cache DB is NOT created here —
        # only on the first record(), so constructing the tracker (e.g.
        # via get_tracker) has no filesystem side effects.
        self.cache_path = (cache_path if cache_path is not None
                           else Path("data/tracker.db"))

    def record(self, job_id: str, stage: str, ts: str,
               note: str | None = None) -> None:
        if stage not in STAGES:
            raise ValueError(
                f"unknown stage {stage!r}; valid stages: {', '.join(STAGES)}"
            )
        payload = json.dumps({"job_id": job_id, "stage": stage,
                              "ts": ts, "note": note})
        self.crm.run("sync", "--json", payload)
        self._mirror(job_id, stage, ts, note)

    def _mirror(self, job_id: str, stage: str, ts: str,
                note: str | None) -> None:
        """Best-effort append to the local SQLite cache.

        Twenty (via crm-bridge) is the source of truth; this mirror only
        supports warehouse sync and offline reads. Any cache failure is
        warned about and swallowed so it never fails the authoritative
        record.
        """
        try:
            SQLiteTracker(self.cache_path).record(job_id, stage, ts, note,
                                                  provenance="twenty")
        except Exception as exc:  # noqa: BLE001 — mirror must never raise
            warnings.warn(
                f"twenty cache mirror to {self.cache_path} failed "
                f"(Twenty record still succeeded): {exc}",
                UserWarning,
                stacklevel=2,
            )

    def current(self, job_id: str) -> dict | None:
        events = [e for e in self.iter_outcomes() if e["job_id"] == job_id]
        if not events:
            return None
        return max(events, key=lambda e: e["ts"])

    def iter_outcomes(self) -> list[dict]:
        now = datetime.now(UTC).isoformat()
        out: list[dict] = []
        for ev in self.crm.fetch_outcomes():
            out.append({
                "job_id": ev.get("job_id"),
                "stage": ev.get("stage"),
                "ts": ev.get("ts"),
                "note": ev.get("note"),
                "provenance": "twenty",
                "recorded_at": ev.get("recorded_at") or now,
            })
        return out
