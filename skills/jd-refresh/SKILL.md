---
name: jd-refresh
description: Refresh job listings by running the discovery scrapers and the enrichment pipeline, then report new and top-ranked jobs so the human can shortlist. Use when asked to refresh job listings, re-scrape the job boards, find new jobs or new postings, re-run the ranked pipeline, or check what is currently ranked best.
---
## Requirements

Python 3.14+ and uv (package manager) are required. Install the toolkit with:

```bash
pip install job-search-toolkit
```

(or `uv tool install job-search-toolkit`).


# jd-refresh

Refresh the job funnel: re-scrape the active boards, upsert them into the
medallion warehouse (`data/warehouse/jobs.db`), incrementally enrich, score,
and export, then report the delta from the gold views — new jobs, jobs that
disappeared, and the top 30 ranked candidates (with posting freshness and any
company news sentiment/notes). The warehouse keeps every job
ever seen; jobs are never deactivated — staleness is inferred from time since
last seen. The agent never shortlists, it presents and stops.

## Playbook

1. **Preflight.**
   - Confirm you are at the repo root: `pwd` should print `C:/Users/evano/repos/job-search-toolkit` (or the current clone path).
   - The enrichment stages need the DeepSeek API key from `.env`; the pipeline fails loudly if it is missing. Do not improvise a fallback.

2. **Run the pipeline** (scrape → upsert → incremental enrich → score → export → gold views):
   ```bash
   job-search-toolkit pipeline run
   ```
   - This materializes the Dagster asset graph (all active boards →
     `silver_upsert` into `data/warehouse/jobs.db` → translate/extract/classify/
     company research → score → exports → gold views).
   - To iterate on a single source without re-scraping the whole set, pass
     `--boards`: `job-search-toolkit pipeline run --boards linkedin_jobs --boards linkedin_posts`
     (merge/score/export/gold still run on the subset).
   - Enrichment is incremental — only new or pending rows hit the LLM; already
     enriched jobs are untouched.
   - The run takes a few minutes. On failure, see "Failure handling".
   - There is NO snapshot step anymore: the bronze directory
     (`data/bronze/{board}/{timestamp}.json` + `runs.json`) and the warehouse
     ARE the history. Jobs are never deactivated — a job "disappears" when it
     has not been seen within the staleness horizon (`STALE_AFTER_DAYS`), which
     is inferred from `last_seen_at`, not a binary flag. This keeps subset
     (`--boards`) runs safe: boards not in the run simply stop refreshing
     `last_seen_at` and eventually fall out of the gold views.

3. **Compute and report the delta from the gold views.** Open the warehouse in
   an eval cell and query the run-scoped views (they are rebuilt on every
   `pipeline run`, so they reflect the run you just executed):

   ```python
   import duckdb
   con = duckdb.connect("data/warehouse/jobs.db")

   new_jobs = con.execute(
       "SELECT j.company, j.title, j.overall_score, j.salary, j.location_raw,"
       "       CAST(DATEDIFF('day', CAST(j.date_posted AS DATE), CURRENT_DATE)"
       "            AS INTEGER) AS days_since_posted,"
       "       c.news_sentiment, c.news_notes "
       "FROM gold.new_this_run j "
       "LEFT JOIN silver.dim_company c ON j.company_id = c.company_id "
       "ORDER BY j.overall_score DESC"
   ).fetchall()

   gone_jobs = con.execute(
       "SELECT company, title, apply_url FROM gold.disappeared_this_run ORDER BY company"
   ).fetchall()

   top30 = con.execute(
       "SELECT j.company, j.title, j.overall_score, j.salary, j.location_raw,"
       "       j.days_since_posted,"
       "       c.news_sentiment, c.news_notes "
       "FROM gold.ranked_jobs j "
       "LEFT JOIN silver.dim_company c ON j.company_id = c.company_id "
       "LIMIT 30"
   ).fetchall()
   ```

   Report — the new-jobs table and the top-30 table use the SAME columns:
   company, role (`title`), `overall_score`, pay range (extract
   `min_annual_eur`–`max_annual_eur` from the `salary` JSON — write "not
   listed" when undisclosed), location (`location_raw`), posting age
   (`days_since_posted`), company sentiment (`news_sentiment`), and a compact
   note drawn from `news_notes` (a JSON array of strings — join the elements with "; " and truncate to ~120 chars). Do NOT print an
   `apply_url` column — the signed clickout URLs are too long to be useful and
   get truncated in a table. The full `apply_url` stays available in the
   warehouse gold views (`gold.new_this_run.apply_url` /
   `gold.ranked_jobs.apply_url`) if the human needs to look one up. Present
   both as markdown tables.
   - **New jobs** (first seen in this run): same columns as the top-30 table,
     ordered by `overall_score` DESC.
   - **Disappeared jobs** (not seen within the staleness horizon): company,
     title, and `apply_url` (list form — likely filled or removed; the URL is
     the only link back to the listing).
   - **Top 30 by `overall_score`** from `gold.ranked_jobs` (which excludes
     stale jobs): same columns as the new-jobs table. Flag any row older than
     30 days as potentially stale so the human can weigh freshness against
     score.

   **Sentiment/notes are honest reads from `silver.dim_company`, joined via
   `company_id`.** Both may be NULL when the enrichment queue has not yet
   processed that company — report that as "not enriched", never as a verdict.
   `news_sentiment` values are `positive` / `negative` / `mixed` /
   `inconclusive`; treat `inconclusive` as a *valid researched state* (no
   headlines found or ambiguous coverage), distinct from missing — never
   re-present it as "not enriched", and never fabricate a sentiment from notes.
   Label the sentiment column "Sentiment (last 90d)": the news enrichment
   lookback is 90 days (`when:90d` on the Google/Bing news search, capped at
   15 headlines per company) — do not describe it as a 30-day window.

4. **Present the shortlist candidates and STOP.**
   - **STOP and present to the human:** a compact report containing (a) the
     count of new jobs, (b) the new-jobs table, (c) the disappeared-jobs list,
     and (d) the top-30 table.
   - End your turn there. The human picks which jobs to shortlist. Do not
     select jobs, add commentary ranking them, or proceed to any application
     work yourself.

5. **Update nothing else.** The CRM (Twenty), application folders
   (`applications/`), resume (`resume/`), and all other repo files are out of
   scope for this skill.

## Failure handling

- If the pipeline run fails (non-zero exit, Dagster materialization failure,
  missing API key, network error), STOP and report the exact error output to
  the human. Do not retry endlessly, do not patch pipeline code, and do not
  improvise infrastructure (no ad-hoc scrapers, no hand-written fallback
  exports).
- If `data/warehouse/jobs.db` or the gold views are missing or unreadable at
  query time, report it and ask the human how to proceed. Never fabricate a
  delta.

## Do not

- Never auto-apply to a job and never shortlist on the human's behalf — the
  human decides.
- Never touch `applications/`, `resume/`, or any file other than
  what the pipeline itself writes (`data/warehouse/jobs.db`, the bronze
  snapshots, and the bridge exports under `data/silver/`).
- Never commit scraped outputs: `data/warehouse/jobs.db`, everything under
  `data/bronze/` and `data/silver/` are gitignored — leave them untracked.
- Never write personal data anywhere except the gitignored paths (`resume/`,
  `applications/`); job-listing data is public, but warehouse
  and bronze outputs stay gitignored (never committed) regardless.
- Never modify pipeline code, schemas, or the Docker services from this skill.
