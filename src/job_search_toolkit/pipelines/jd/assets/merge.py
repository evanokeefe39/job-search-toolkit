"""Merge asset: union all source boards, deduplicate by id."""

from __future__ import annotations

import json

import dagster as dg

from .common import FREEWORK_RAW, HIRINGCAFE_RAW, MERGED_JOBS
from .scrape import freework_jobs, hiringcafe_jobs


@dg.asset(
    deps=[freework_jobs, hiringcafe_jobs],
    group_name="processing",
    description="Merged canonical jobs from all boards, deduplicated",
)
def merged_jobs() -> dg.MaterializeResult:
    """Load all source files, merge, deduplicate by id."""
    all_jobs: list[dict] = []
    seen: set[str] = set()
    sources_used: list[str] = []

    for path, label in [
        (FREEWORK_RAW, "freework"),
        (HIRINGCAFE_RAW, "hiringcafe"),
    ]:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for job in data:
                jid = job.get("id", "")
                if jid and jid in seen:
                    continue
                if jid:
                    seen.add(jid)
                all_jobs.append(job)
        sources_used.append(
            f"{label}: {len(data) if isinstance(data, list) else 1}"
        )

    MERGED_JOBS.write_text(
        json.dumps(all_jobs, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return dg.MaterializeResult(metadata={
        "total_jobs": len(all_jobs),
        "sources": ", ".join(sources_used),
    })
