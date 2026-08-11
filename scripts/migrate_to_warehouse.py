"""One-time migration: populate the DuckDB warehouse from the existing files.

Run 1 (historical backfill): imports the prior ranked CSV snapshot
(``data/_tmp_jobs_ranked_prior_20260810_200016.csv``) as the oldest run —
this is what gives ``first_seen``/``last_seen`` history on day one.

Run 2 (current state): imports ``data/silver/merged_jobs.json``. Rows that
also appear in the backfill keep their earlier ``first_seen`` (matched by
``apply_url``, since the CSV has no stable board id); rows seen only in the
CSV become the first "disappeared" jobs (inactive); rows only in the JSON
are genuinely new.

Enrichment completeness is derived from column presence, not the retired
``_enrichment`` dict: rows missing any stage output (notably all 111 freework
rows, whose company research was deferred) get ``enriched_at = NULL`` and are
picked up by the next pipeline run.

Usage: uv run python scripts/migrate_to_warehouse.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from job_search_toolkit.pipelines.jd import silver as S
from job_search_toolkit.pipelines.jd.assets.common import append_bronze_run
from job_search_toolkit.pipelines.jd.config import BRONZE_RUNS, SILVER_DIR, WAREHOUSE_DB
from job_search_toolkit.pipelines.jd.gold import build_gold

PRIOR_RUN = "prior_20260810_200016"
MIGRATION_RUN = "migration"
PRIOR_SNAPSHOT = Path("data/_tmp_jobs_ranked_prior_20260810_200016.csv")
MERGED = SILVER_DIR / "merged_jobs.json"


def _f(value: str | None) -> float | None:
    """Parse a CSV number cell (empty string → None)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _i(value: str | None) -> int | None:
    f = _f(value)
    return int(f) if f is not None else None


def _split(value: str | None) -> list[str]:
    return [x for x in (value or "").split("|") if x]


def _csv_to_job(row: dict, merged_by_url: dict) -> dict | None:
    """Reconstruct a canonical record from a ranked CSV row.

    The CSV has no stable board id (hiringcafe ids are objectIDs, freework
    ids are source URLs), so rows matching a ``merged_jobs.json`` record by
    ``apply_url`` adopt its id; unmatched rows (the first "disappeared"
    jobs) use ``apply_url`` as a synthetic id.
    """
    apply_url = (row.get("apply_url") or "").rstrip("/")
    merged = merged_by_url.get(apply_url)
    jid = merged["id"] if merged else (apply_url or None)
    if not jid:
        return None

    salary_min = _f(row.get("salary_min_annual_eur"))
    salary_max = _f(row.get("salary_max_annual_eur"))
    return {
        "id": jid,
        "source_board": row.get("source_board") or "freework",
        "source_url": apply_url or None,
        "title": row.get("title") or "",
        "company": row.get("company") or "",
        "apply_url": apply_url or None,
        "location_raw": row.get("location_raw") or "",
        "workplace_type": row.get("workplace_type") or None,
        "date_posted": row.get("date_posted") or None,
        "salary": {
            "min_annual_eur": salary_min,
            "max_annual_eur": salary_max,
            "currency_original": "EUR",
            "frequency_original": "yearly",
            "is_disclosed": bool(salary_min or salary_max),
        },
        "contract_types": _split(row.get("contract_types")),
        "seniority_level": row.get("seniority_level") or None,
        "role_category": row.get("role_category") or None,
        "years_experience_min": _i(row.get("years_experience_min")),
        "technologies": _split(row.get("technologies")),
        "competencies": _split(row.get("competencies")),
        "description_text": "",
        "description_language": None,
        "company_info": {
            "name": row.get("company") or "",
            "industry": _split(row.get("company_industry")),
            "size_employees": _i(row.get("company_size")),
            "year_founded": _i(row.get("company_founded")),
            "org_type": row.get("company_type") or "unknown",
            "stock_symbol": row.get("company_stock_symbol") or None,
        },
        "engagement_type": row.get("engagement_type") or None,
        "posting_company_type": row.get("posting_company_type") or None,
        "end_client_name": row.get("end_client_name") or None,
        "end_client_sector": row.get("end_client_sector") or None,
        "contract_duration": row.get("contract_duration") or None,
        "scores": {
            k: _f(row.get(f"scores_{k}"))
            for k in ("pay", "flexibility", "low_responsibility", "tech_match", "company_quality")
        },
        "overall_score": _f(row.get("overall_score")),
        "recommendation_tier": row.get("recommendation_tier") or None,
    }


def _mark_enriched_for_run(con, run_id: str) -> None:
    """Set enriched_at on fully-enriched rows of one run, keeping scores."""
    con.execute(
        f"UPDATE silver.jobs SET enriched_at = NOW() "
        f"WHERE enriched_at IS NULL AND last_seen_run = {S.sql_literal(run_id)} "
        f"AND {S.DONE_ALL}"
    )


def main() -> None:
    if not MERGED.exists():
        sys.exit(f"missing {MERGED} — nothing to migrate")
    if not PRIOR_SNAPSHOT.exists():
        sys.exit(
            f"missing prior snapshot {PRIOR_SNAPSHOT} — the historical backfill "
            "needs it; drop the backfill from this script if it is gone"
        )

    merged = json.loads(MERGED.read_text(encoding="utf-8"))
    # Align with the adapter's semantics: NULL org_type/engagement_type means
    # "not yet LLM-processed" (the old adapter emitted 'unknown' for both).
    for j in merged:
        if j.get("source_board") == "freework":
            ci = j.get("company_info") or {}
            if ci.get("org_type") == "unknown":
                ci["org_type"] = None
            if j.get("engagement_type") == "unknown":
                j["engagement_type"] = None
    merged_by_url = {
        (j.get("apply_url") or "").rstrip("/"): j for j in merged if j.get("apply_url")
    }
    with PRIOR_SNAPSHOT.open(newline="", encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
    csv_jobs = [j for j in (_csv_to_job(r, merged_by_url) for r in csv_rows) if j]
    print(f"backfill: {len(csv_jobs)} CSV rows | merged: {len(merged)} records")

    with S.connect() as con:
        columns = S.ensure_jobs_table(con, csv_jobs + merged)

        # Run 1 — historical backfill (oldest run).
        S.upsert_run(con, PRIOR_RUN, csv_jobs, columns)
        _mark_enriched_for_run(con, PRIOR_RUN)

        # Run 2 — current state; full content replace for matched rows.
        S.upsert_run(con, MIGRATION_RUN, merged, columns, full_replace=True)
        _mark_enriched_for_run(con, MIGRATION_RUN)

        # The first "disappeared" jobs: in the backfill, not in the JSON.
        S.deactivate_not_seen(con, MIGRATION_RUN)

        stats = con.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN is_active THEN 1 ELSE 0 END),
                   SUM(CASE WHEN enriched_at IS NULL THEN 1 ELSE 0 END)
            FROM silver.jobs
            """
        ).fetchone()
        total, active, pending = (int(x or 0) for x in stats)
        print(f"warehouse: {total} rows | {active} active | {pending} enrichment-pending")

    build_gold(WAREHOUSE_DB, run_id=MIGRATION_RUN)
    print(f"gold views created in {WAREHOUSE_DB}")

    # Informational manifest entries (the migration is not a bronze scrape).
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    for board, count in (("freework", 1), ("hiringcafe", 1)):
        append_bronze_run(
            MIGRATION_RUN, board, ts,
            f"{board}/merged_jobs.json (migration import)", count,
        )
    print(f"manifest entry written to {BRONZE_RUNS}")


if __name__ == "__main__":
    main()
