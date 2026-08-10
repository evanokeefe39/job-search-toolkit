"""Shared paths and helpers used across asset modules."""

from __future__ import annotations

import json

from ..config import BRONZE_DIR, SILVER_DIR

# Medallion paths for the JD pipeline assets.
FREEWORK_RAW = BRONZE_DIR / "freework_jobs.json"
HIRINGCAFE_RAW = BRONZE_DIR / "hiringcafe_jobs.json"
MERGED_JOBS = SILVER_DIR / "merged_jobs.json"
RANKED_CSV = SILVER_DIR / "jobs_ranked.csv"


def load_merged() -> list[dict]:
    return json.loads(MERGED_JOBS.read_text(encoding="utf-8"))


def save_merged(jobs: list[dict]) -> None:
    MERGED_JOBS.write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8"
    )
