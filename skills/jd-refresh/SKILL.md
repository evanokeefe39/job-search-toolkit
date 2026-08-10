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

Refresh the job funnel: re-scrape both boards, merge, enrich, score, and export a
new `data/silver/jobs_ranked.csv`, then diff it against the previous run and present the
delta — new jobs, jobs that disappeared, and the top 10 ranked candidates.
The agent never shortlists; it presents and stops.

## Playbook

1. **Preflight.**
   - Confirm you are at the repo root: `pwd` should print `C:/Users/evano/repos/job-search-toolkit` (or the current clone path).
   - The enrichment stages need the DeepSeek API key from `.env`; the pipeline fails loudly if it is missing. Do not improvise a fallback.

2. **Snapshot the prior ranked output** (before running anything).
   - If `data/silver/jobs_ranked.csv` exists, copy it to a gitignored path
     so a delta can be computed without ever committing the snapshot (the global
     `*.csv` rule ignores anything under `data/`; do NOT use `/tmp` — it does not
     exist on this Windows git-bash):
     ```bash
     mkdir -p data && cp data/silver/jobs_ranked.csv data/_tmp_jobs_ranked_prior_$(date +%Y%m%d_%H%M%S).csv
     ```
   - Note the exact snapshot path you created — you diff against it in step 4.
   - If `data/silver/jobs_ranked.csv` does not exist, this is the first run: there is no
     prior baseline, so every job in the new output counts as "new". State that
     in the report.

3. **Run the pipeline** (scrape → merge → enrich → score → export):
   ```bash
   job-search-toolkit pipeline run
   ```
   - This materializes the full Dagster asset graph (both boards → merge →
     translate/extract/classify/company research → score → export).
   - Outputs (under `data/silver/`, gitignored): `data/silver/jobs_ranked.csv`
     (ranked list) and `data/silver/merged_jobs.json` (canonical records).
   - Enrichment is idempotent — already-processed jobs are skipped on re-runs.
   - The run takes several minutes (LLM enrichment). On failure, see
     "Failure handling".

4. **Compute and report the delta.** Diff the NEW `data/silver/jobs_ranked.csv` against the
   snapshot from step 2, keyed on `apply_url` (the unique posting URL — the
    stable id across runs; it is also the `id` field in `data/silver/merged_jobs.json`).
   Report:
   - **New jobs** (present in the new file, absent from the snapshot): for each,
     company, title, and `apply_url`.
   - **Disappeared jobs** (present in the snapshot, absent from the new file):
     for each, company, title, and `apply_url` — likely filled or removed.
   - **Top 10 by `overall_score`** from the new file: for each job, report
     company, role (`title`), `overall_score`, pay range
     (`salary_min_annual_eur`–`salary_max_annual_eur`), location
     (`location_raw`), and URL (`apply_url`). Write "not listed" for an empty
     pay field.
   - Use exactly these CSV columns: `overall_score`, `company`, `title`,
     `salary_min_annual_eur`, `salary_max_annual_eur`, `location_raw`,
     `apply_url`.

5. **Present the shortlist candidates and STOP.**
   - **STOP and present to the human:** a compact report containing (a) the
     count of new jobs, (b) the new-jobs list, (c) the disappeared-jobs list,
     and (d) the top-10 table.
   - End your turn there. The human picks which jobs to shortlist. Do not
     select jobs, add commentary ranking them, or proceed to any application
     work yourself.

6. **Update nothing else.** The tracker (`tracker.csv`), application folders
   (`applications/`), resume (`resume/`), and all other repo files are out of
   scope for this skill.

## Failure handling

- If the pipeline run fails (non-zero exit, Dagster materialization failure,
  missing API key, network error), STOP and report the exact error output to
  the human. Do not retry endlessly, do not patch pipeline code, and do not
  improvise infrastructure (no ad-hoc scrapers, no hand-written fallback
  exports).
- If the snapshot or the new `data/silver/jobs_ranked.csv` is missing or unreadable at diff
  time, report it and ask the human how to proceed. Never fabricate a delta.

## Do not

- Never auto-apply to a job and never shortlist on the human's behalf — the
  human decides.
- Never touch `applications/`, `resume/`, `tracker.csv`, or any file other than
  what the pipeline itself writes (`data/silver/jobs_ranked.csv`,
  `data/silver/merged_jobs.json`) and the gitignored snapshot from step 2.
- Never commit scraped outputs: `data/silver/jobs_ranked.csv`,
  `data/silver/merged_jobs.json`, and the scraper outputs under `data/bronze/`
  are gitignored — leave them untracked.
- Never write personal data anywhere except the gitignored paths (`resume/`,
  `applications/`, `tracker.csv`); job-listing data is public, but the snapshot
  stays gitignored (never committed) regardless.
- Never modify pipeline code, schemas, or the Docker services from this skill.
