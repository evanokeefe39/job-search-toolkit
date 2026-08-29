"""Outcome tracker package (WS1 Epic 1.1).

Pluggable append-only outcome feed: SQLite backend (default,
zero-install) or Twenty CRM backend via crm-bridge. Selected through
``config.yaml``::

    tracker:
      backend: sqlite   # or "twenty"

Every event dict has at least {job_id, stage, ts, note, provenance,
recorded_at}; provenance is "sqlite" or "twenty".
"""

from __future__ import annotations

from pathlib import Path

from job_search_toolkit.configutil import DEFAULT_CONFIG_PATH, load_config_file
from job_search_toolkit.tracker.protocol import STAGES, Tracker
from job_search_toolkit.tracker.sqlite_backend import SQLiteTracker
from job_search_toolkit.tracker.twenty_backend import CRMCommand, TwentyTracker

__all__ = [
    "STAGES",
    "Tracker",
    "SQLiteTracker",
    "TwentyTracker",
    "CRMCommand",
    "get_tracker",
]

_BACKENDS = ("sqlite", "twenty")


def get_tracker(config: dict | None = None,
                db_path: Path | None = None) -> Tracker:
    """Factory.

    Reads ``config["tracker"]["backend"]`` (default "sqlite"; when
    ``config`` is None, loads ``config.yaml`` — a missing file yields
    sqlite). Unknown backend -> ValueError listing the valid backends.
    ``db_path`` overrides the sqlite DB location (default ``data/tracker.db``).
    """
    if config is None:
        config = load_config_file(DEFAULT_CONFIG_PATH)
    backend = ((config.get("tracker") or {}).get("backend")) or "sqlite"
    if backend not in _BACKENDS:
        raise ValueError(
            f"unknown tracker backend {backend!r}; valid backends: "
            + ", ".join(repr(b) for b in _BACKENDS)
        )
    if backend == "twenty":
        return TwentyTracker()
    return SQLiteTracker(db_path=db_path if db_path is not None
                         else Path("data/tracker.db"))
