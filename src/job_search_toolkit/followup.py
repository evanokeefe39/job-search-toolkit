"""Follow-up queue and draft recording (WS5 Epic 5.2).

Pure functions, dagster-free. The queue answers: which applications are still
sitting in ``applied`` past the follow-up threshold with no reply and with
room for another follow-up? Drafts are recorded in the application folder's
status.yaml only — nothing is ever sent by this module; the human sends.

Cadence note: the threshold uses **calendar days**
(``(now - applied_ts).days``). This intentionally ignores weekends/business
days; the business-day skew is documented here, not computed — a follow-up
may fire one or two calendar days "late" relative to a business-day reading,
which is acceptable for a gentle nudge cadence.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from job_search_toolkit.status import add_followup, followup_count
from job_search_toolkit.status import read_status

__all__ = [
    "FOLLOWUP_THRESHOLD_DAYS",
    "MAX_FOLLOWUPS",
    "followups_due",
    "draft_followup",
]

FOLLOWUP_THRESHOLD_DAYS = 10
"""Calendar days after an ``applied`` event before an application is due."""

MAX_FOLLOWUPS = 2
"""Maximum number of draft follow-ups per application; further drafts raise
:class:`~job_search_toolkit.status.FollowupCapError`."""

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_(.+)$")


def _parse_slug(name: str) -> tuple[str, str]:
    """Split a folder name into ``(company, role)``.

    Strips the leading ``YYYY-MM-DD_`` prefix when present; the remainder is
    underscore-separated with the first token as company and everything after
    it as the role slug.
    """
    m = _DATE_PREFIX.match(name)
    rest = m.group(1) if m else name
    parts = rest.split("_")
    if len(parts) < 2:
        return rest, ""
    return parts[0], "_".join(parts[1:])


def _latest_applied_ts(status: dict) -> datetime | None:
    """Latest ``applied`` transition ts from the status.yaml history."""
    ts: str | None = None
    for tr in status.get("transitions", []):
        if tr.get("stage") == "applied":
            ts = tr.get("ts")
    if ts is None:
        return None
    return datetime.fromisoformat(ts)


def followups_due(
    apps_root: Path,
    tracker,
    days: int = FOLLOWUP_THRESHOLD_DAYS,
    now: datetime | None = None,
) -> list[dict]:
    """Return applications due for a follow-up.

    An application folder under ``apps_root`` is included when:

    - it contains a ``status.yaml``,
    - the tracker's **current** stage for the folder slug is exactly
      ``applied`` (anything later — interview/offer — or terminal —
      rejected/withdrawn/ghosted — excludes it),
    - the latest ``applied`` transition in status.yaml is more than ``days``
      calendar days old (relative to ``now``, injected for determinism),
    - the folder has fewer than ``MAX_FOLLOWUPS`` recorded follow-ups.

    Rows are ``{slug, folder, company, role, days_since_applied,
    followup_count}`` sorted by ``days_since_applied`` descending (slug as a
    deterministic tiebreaker).

    Corrupt/unreadable status.yaml propagates the loud failure from
    :func:`job_search_toolkit.status.read_status` — never silently skipped.
    """
    now = now or datetime.now()
    due: list[dict] = []
    for folder in sorted(apps_root.iterdir()):
        if not folder.is_dir():
            continue
        status = read_status(folder)
        if status is None:
            continue
        slug = folder.name
        current = tracker.current(slug)
        if current is None or current.get("stage") != "applied":
            continue
        applied_ts = _latest_applied_ts(status)
        if applied_ts is None:
            continue
        days_since = (now - applied_ts).days
        if days_since <= days:
            continue
        count = followup_count(folder)
        if count >= MAX_FOLLOWUPS:
            continue
        company, role = _parse_slug(slug)
        due.append(
            {
                "slug": slug,
                "folder": folder,
                "company": company,
                "role": role,
                "days_since_applied": days_since,
                "followup_count": count,
            }
        )
    due.sort(key=lambda row: (-row["days_since_applied"], row["slug"]))
    return due


def draft_followup(folder: Path, ts: str, note: str) -> dict:
    """Record a follow-up draft in the folder's status.yaml.

    Draft-only by construction: the note is appended to the ``followups``
    list (never a tracker event, never sent). Raises
    :class:`~job_search_toolkit.status.FollowupCapError` once
    ``MAX_FOLLOWUPS`` drafts already exist. Returns the updated status dict.
    """
    return add_followup(folder, ts, note)
