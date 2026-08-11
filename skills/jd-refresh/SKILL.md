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

Refresh the job funnel: re-scrape both boards, upsert them into the medallion
warehouse (`data/warehouse/jobs.db`), incrementally enrich, score, and export,
then report the delta from the gold views — new jobs, jobs that disappeared,
and the top 10 ranked candidates. The warehouse keeps every job ever seen;
the agent never shortlists, it presents and stops.

## Playbook

1. **Preflight.**
   - Confirm you are at the repo root: `pwd` should print `C:/Users/evano/repos/job-search-toolkit` (or the current clone path).
   - The enrichment stages need the DeepSeek API key from `.env`; the pipeline fails loudly if it is missing. Do not improvise a fallback.

2. **Run the pipeline** (scrape → upsert → incremental enrich → score → export → gold views):
   ```bash
   job-search-toolkit pipeline run
   ```
   - This materializes the full Dagster asset graph (both boards → `silver_upsert`
     into `data/warehouse/jobs.db` → translate/extract/classify/company research →
     score → exports → gold views).
   - Enrichment is incremental — only new or pending rows hit the LLM; already
     enriched jobs are untouched.
   - The run takes a few minutes. On failure, see "Failure handling".
   - There is NO snapshot step anymore: the bronze directory
     (`data/bronze/{board}/{timestamp}.json` + `runs.json`) and the warehouse
     ARE the history. Disappeared jobs are marked `is_active = false`, never deleted.

3. **Compute and report the delta from the gold views.** Open the warehouse in
   an eval cell and query the run-scoped views (they are rebuilt on every
   `pipeline run`, so they reflect the run you just executed):

   ```python
   import duckdb
   con = duckdb.connect("data/warehouse/jobs.db")

   new_jobs = con.execute(
       "SELECT company, title, apply_url FROM gold.new_this_run ORDER BY company"
   ).fetchall()

   gone_jobs = con.execute(
       "SELECT company, title, apply_url FROM gold.disappeared_this_run ORDER BY company"
   ).fetchall()

   top10 = con.execute(
       "SELECT company, title, overall_score, salary, location_raw, apply_url "
       "FROM gold.ranked_jobs LIMIT 10"
   ).fetchall()
   ```

   Report:
   - **New jobs** (first seen in this run): for each, company, title, and `apply_url`.
   - **Disappeared jobs** (active last run, inactive now): for each, company,
     title, and `apply_url` — likely filled or removed.
   - **Top 10 by `overall_score`** from the new file: for each job, report
     company, role (`title`), `overall_score`, pay range (extract
     `min_annual_eur`–`max_annual_eur` from the `salary` JSON — write "not
     listed" when undisclosed), location (`location_raw`), and URL (`apply_url`).

4. **Present the shortlist candidates and STOP.**
   - **STOP and present to the human:** a compact report containing (a) the
     count of new jobs, (b) the new-jobs list, (c) the disappeared-jobs list,
     and (d) the top-10 table.
   - End your turn there. The human picks which jobs to shortlist. Do not
     select jobs, add commentary ranking them, or proceed to any application
     work yourself.

5. **Update nothing else.** The tracker (`tracker.csv`), application folders
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
- Never touch `applications/`, `resume/`, `tracker.csv`, or any file other than
  what the pipeline itself writes (`data/warehouse/jobs.db`, the bronze
  snapshots, and the bridge exports under `data/silver/`).
- Never commit scraped outputs: `data/warehouse/jobs.db`, everything under
  `data/bronze/` and `data/silver/` are gitignored — leave them untracked.
- Never write personal data anywhere except the gitignored paths (`resume/`,
  `applications/`, `tracker.csv`); job-listing data is public, but warehouse
  and bronze outputs stay gitignored (never committed) regardless.
- Never modify pipeline code, schemas, or the Docker services from this skill.
