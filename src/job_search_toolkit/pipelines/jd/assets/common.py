"""Shared paths and helpers used across asset modules."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import BRONZE_DIR, BRONZE_RUNS

# Medallion paths for the JD pipeline assets.
FREEWORK_RAW = BRONZE_DIR / "freework_jobs.json"
HIRINGCAFE_RAW = BRONZE_DIR / "hiringcafe_jobs.json"
HELLOWORK_RAW = BRONZE_DIR / "hellowork_jobs.json"
ENGLISHJOBS_RAW = BRONZE_DIR / "englishjobs_jobs.json"
FARUSE_RAW = BRONZE_DIR / "faruse_jobs.json"
WWR_RAW = BRONZE_DIR / "wwr_jobs.json"
REMOTEOK_RAW = BRONZE_DIR / "remoteok_jobs.json"
DATASCIENCEJOBS_RAW = BRONZE_DIR / "datasciencejobs_jobs.json"

# Per-board bronze history directories (immutable timestamped snapshots).
BRONZE_BOARD_DIRS = {
    "freework": BRONZE_DIR / "freework",
    "hiringcafe": BRONZE_DIR / "hiringcafe",
    "hellowork": BRONZE_DIR / "hellowork",
    "englishjobs": BRONZE_DIR / "englishjobs",
    "faruse": BRONZE_DIR / "faruse",
    "wwr": BRONZE_DIR / "wwr",
    "remoteok": BRONZE_DIR / "remoteok",
    "datasciencejobs": BRONZE_DIR / "datasciencejobs",
}


def iso_timestamp() -> str:
    """UTC timestamp for bronze filenames: 2026-08-10T200055Z.

    No colons — Windows filesystem-safe; matches the run timestamp format
    used by the jd-refresh skill's snapshots.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def bronze_timestamped_path(board: str, ts: str | None = None) -> Path:
    """Path for this run's immutable bronze snapshot of ``board``."""
    return BRONZE_BOARD_DIRS[board] / f"{(ts or iso_timestamp())}.json"


def append_bronze_run(run_id: str, board: str, ts: str, file_rel: str, job_count: int) -> None:
    """Append a run entry to ``data/bronze/runs.json`` (created if missing).

    The manifest tracks every scrape: which runs saw which jobs. Entry shape:
    ``{run_id, board, timestamp, file, job_count}``. ``run_id`` is the Dagster
    run id, shared by both boards in one ``pipeline run``.
    """
    entries: list[dict] = []
    if BRONZE_RUNS.exists():
        entries = json.loads(BRONZE_RUNS.read_text(encoding="utf-8"))
    entries.append({
        "run_id": run_id,
        "board": board,
        "timestamp": ts,
        "file": file_rel,
        "job_count": job_count,
    })
    BRONZE_RUNS.parent.mkdir(parents=True, exist_ok=True)
    BRONZE_RUNS.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
    )


