# data/ — medallion layout

The pipeline stores data in three layers, mirroring the classic medallion
architecture. Jobs are never deleted: when a listing disappears from the
boards it is marked inactive but stays queryable.

```
data/
├── bronze/    immutable per-run snapshots of every scrape
├── silver/    bridge exports materialized from the warehouse (consumer files)
└── warehouse/ DuckDB database: silver.jobs table + gold.* analytics views
```

## bronze/ — immutable raw ingest

One timestamped file per scrape, per board:

- `data/bronze/freework/2026-08-10T200055Z.json`
- `data/bronze/hiringcafe/2026-08-10T200055Z.json`

Produced by `job-search-toolkit pipeline run` (the `freework_jobs` /
`hiringcafe_jobs` assets). File naming is the scraper start time (UTC, no
colons — Windows-safe). A `data/bronze/runs.json` manifest records every
run: `[{run_id, board, timestamp, file, job_count}]`. The bronze directory
IS the history — the jd-refresh skill's old snapshot-then-diff dance is gone.

The flat live files (`freework_jobs.json`, `hiringcafe_jobs.json`) are still
written for the scrapers' own CLI, but the pipeline ingests from the
timestamped snapshots.

## warehouse/ — DuckDB: silver + gold

- `data/warehouse/jobs.db` — single DuckDB file with two schemas:

  - `silver.jobs` — one row per unique `(id, source_board)`; all canonical
    fields as columns (nested dicts/lists as JSON) plus lineage:
    `first_seen_run`/`first_seen_at`, `last_seen_run`/`last_seen_at`,
    `is_active`, `enriched_at`, `enrichment_version`, `created_at`,
    `updated_at`. Upserted on every run with
    `ON CONFLICT (id, source_board) DO UPDATE` — re-scrapes update
    `last_seen`/`is_active` only, enrichment is preserved.
  - `gold.*` — analytics views, `CREATE OR REPLACE` each run: `ranked_jobs`,
    `by_sector`, `by_tier`, `job_history`, `weekly_snapshot`,
    `new_this_run`, `disappeared_this_run`.

Enrichment is incremental: each stage queries only rows its gate selects
(column nullability — see `src/job_search_toolkit/pipelines/jd/silver.py`)
and writes results back with UPDATEs. `enriched_at = NULL` means pending;
bumping `ENRICHMENT_VERSION` (config) forces re-enrichment.

## silver/ — bridge exports (consumer files)

The agent skills read files by path, so the pipeline materializes them from
the warehouse on every run (`COPY ... TO ... (ARRAY true)`):

- `data/silver/jobs_ranked.csv` — active scored jobs, flattened, ordered by
  `overall_score` DESC (jd-refresh / new-application input rows)
- `data/silver/merged_jobs.json` — all active canonical records (new-application
  primary lookup; superseded by direct DuckDB queries in the updated skill)
- `data/silver/freework_jobs_enriched.json` — active freework-only records

These are exports, not the source of truth — the warehouse is.

Example:

```bash
uv run python -c "
import duckdb
con = duckdb.connect('data/warehouse/jobs.db')
print(con.sql('SELECT * FROM gold.by_tier').df())
"
```

`data/_tmp_*` files are scratch artifacts (gitignored); ignore them.
