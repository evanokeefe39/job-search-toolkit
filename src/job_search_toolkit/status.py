"""Application status.yaml (WS5 Epic 5.1).

Every application folder (``applications/YYYY-MM-DD_<company>_<role>/``) owns
a ``status.yaml`` with an append-only transition history and follow-up drafts.
The folder NAME is the tracker ``job_id``. Each recorded outcome is written
both to status.yaml (via :func:`write_transition`) and to the shared tracker
(via :func:`record_outcome`). Corrupt status files fail loudly — never
silently overwritten.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from job_search_toolkit.tracker import STAGES, get_tracker

STATUS_FILE = "status.yaml"

MAX_FOLLOWUPS = 2


class CorruptStatusError(Exception):
    """Raised when an existing status.yaml is unreadable or invalid.

    Recovery: the file is preserved untouched — repair or delete it by hand
    (with the human's approval) and re-record the history from the tracker
    feed, which is the authoritative append-only source.
    """


class FollowupCapError(Exception):
    """Raised when a third follow-up draft would exceed the cap of 2."""


def _status_path(folder: Path) -> Path:
    return folder / STATUS_FILE


def _dump_atomic(path: Path, data: dict) -> None:
    """Write YAML atomically: temp file in the same dir, then os.replace."""
    text = yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".status-", suffix=".yaml.tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_status(folder: Path) -> dict | None:
    """Read status.yaml inside *folder*.

    Returns None when no status file exists. Raises CorruptStatusError on an
    unreadable, invalid-YAML, or schema-corrupt file — the file is never
    silently overwritten.
    """
    path = _status_path(folder)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise CorruptStatusError(
            f"{path} is unreadable or invalid YAML ({exc!r}). "
            "Recovery: repair or remove the file manually before recording "
            "new transitions; the tracker feed keeps the authoritative "
            "append-only history."
        ) from exc
    if not isinstance(data, dict):
        raise CorruptStatusError(
            f"{path} does not contain a YAML mapping (got "
            f"{type(data).__name__}). Recovery: repair or remove the file "
            "manually before recording new transitions; the tracker feed "
            "keeps the authoritative append-only history."
        )
    transitions = data.get("transitions", [])
    if not isinstance(transitions, list):
        raise CorruptStatusError(
            f"{path}: 'transitions' must be a list. Recovery: repair the "
            "file manually; the tracker feed keeps the authoritative history."
        )
    followups = data.get("followups", [])
    if not isinstance(followups, list):
        raise CorruptStatusError(
            f"{path}: 'followups' must be a list. Recovery: repair the file "
            "manually before adding follow-up drafts."
        )
    return data


def current_stage(folder: Path) -> str | None:
    """Latest stage from status.yaml, or None when unknown/absent."""
    status = read_status(folder)
    if status is None:
        return None
    return status.get("current_stage")


def write_transition(folder: Path, stage: str, ts: str,
                     note: str | None = None) -> dict:
    """Append one stage transition to status.yaml (append-only, idempotent).

    Validates *stage* against the tracker vocabulary (ValueError otherwise).
    Creates a fresh status file (created_at = *ts*) when none exists. Never
    mutates prior entries; the write is atomic (temp file + os.replace).
    Returns the updated status dict.
    """
    if stage not in STAGES:
        raise ValueError(
            f"unknown stage {stage!r}; valid stages: {', '.join(STAGES)}"
        )
    status = read_status(folder)
    if status is None:
        status = {
            "folder": f"applications/{folder.name}",
            "current_stage": None,
            "created_at": ts,
            "transitions": [],
            "followups": [],
        }
    event: dict = {"stage": stage, "ts": ts}
    if note is not None:
        event["note"] = note
    if event not in status["transitions"]:
        status["transitions"].append(event)
    status["current_stage"] = stage
    _dump_atomic(_status_path(folder), status)
    return status


def followup_count(folder: Path) -> int:
    """Number of follow-up drafts recorded in status.yaml."""
    status = read_status(folder)
    if status is None:
        return 0
    return len(status.get("followups", []))


def add_followup(folder: Path, ts: str, note: str) -> dict:
    """Append a follow-up DRAFT to status.yaml (never sent, no tracker event).

    Raises FollowupCapError when there are already 2 drafts. Returns the
    updated status dict.
    """
    status = read_status(folder)
    if status is None:
        status = {
            "folder": f"applications/{folder.name}",
            "current_stage": None,
            "created_at": ts,
            "transitions": [],
            "followups": [],
        }
    followups = status["followups"]
    if len(followups) >= MAX_FOLLOWUPS:
        raise FollowupCapError(
            f"follow-up cap reached: {len(followups)} drafts already recorded "
            f"in {_status_path(folder)}; drafts are never sent and the cap "
            "is 2 — decide the next action with the human."
        )
    followups.append({"ts": ts, "note": note})
    _dump_atomic(_status_path(folder), status)
    return status


def record_outcome(folder: Path, stage: str, ts: str,
                   note: str | None = None,
                   config: dict | None = None,
                   db_path: Path | None = None) -> dict:
    """Record an outcome on an application: status.yaml THEN the tracker.

    ``folder.name`` is the tracker ``job_id``. Returns the updated status
    dict.
    """
    status = write_transition(folder, stage, ts, note=note)
    get_tracker(config=config, db_path=db_path).record(
        folder.name, stage, ts, note
    )
    return status
