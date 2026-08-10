# data/ — medallion layout

The pipeline stores data in three layers, mirroring the classic medallion
architecture:

```
data/
├── bronze/   raw canonical records, one file per job board
├── silver/   merged + enriched + scored canonical jobs
└── gold/     DuckDB analytics database (views, not files)
```

## bronze/ — raw canonical per board

One JSON file per source board, in the normalized `CanonicalJob` shape
(`src/job_search_toolkit/schemas.py`):

- `data/bronze/freework_jobs.json`
- `data/bronze/hiringcafe_jobs.json`

Produced by `job-search-toolkit scrape freework|hiringcafe`.

## silver/ — merged + enriched + scored

- `data/silver/merged_jobs.json` — the deduplicated union of all bronze
  boards, enriched (translation, tech extraction, company stats) and scored
  (`scores`, `overall_score`, `recommendation_tier`).
- `data/silver/jobs_ranked.csv` — the same dataset as a flat CSV ordered by
  `overall_score` DESC, for quick spreadsheet review.

Produced by `job-search-toolkit pipeline run`.

## gold/ — DuckDB views for analytics

- `data/gold/jobs.db` — a DuckDB database built from
  `data/silver/merged_jobs.json` by `job-search-toolkit pipeline gold`
  (`job_search_toolkit.pipeline.gold.build_gold`).

The `jobs` table flattens each job's top-level fields into columns; nested
structures (`salary`, `company_info`, `scores`, `_source`, …) are kept as
JSON strings. Three ready-made views:

| view          | contents                                             |
|---------------|------------------------------------------------------|
| `ranked_jobs` | scored jobs, ordered by `overall_score` DESC         |
| `by_sector`   | job counts grouped by `end_client_sector`            |
| `by_tier`     | job counts grouped by `recommendation_tier`          |

Rebuilding is safe — `build_gold` (re)creates the table and views on every
run.

Example:

```bash
uv run python -c "
import duckdb
con = duckdb.connect('data/gold/jobs.db')
print(con.sql('SELECT * FROM by_tier').df())
"
```

`data/_tmp_*` files are scratch artifacts (gitignored); ignore them.
