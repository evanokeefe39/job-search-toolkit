# HANDOFF.md — jd-refresh funnel + pipeline hardening

## Goal

Keep the job-search-toolkit funnel healthy from the box. The immediate
deliverable is to restore a **clean end-to-end `job-search-toolkit pipeline
run`** (all active boards scrape → upsert → score → export → gold views) and
prove the delta-report skill path works, because this session found two real
pipeline defects that abort every full run before gold/exports finish. Fix
those first, then run a full refresh and report the delta so the human can
shortlist. Secondary: investigate the hiringcafe.com 403 (scraper
maintenance, separate from the funnel bugs).

## Definition of done

- [ ] A full `job-search-toolkit pipeline run` completes with `RUN_SUCCESS`
      (no `ranked_csv` step failure). done when:
      `job-search-toolkit pipeline run` → exit 0, and
      `grep -c "STEP_FAILURE"` on its log is 0.
- [ ] The post-`RUN_FAILURE` dagster process no longer hangs (or is explicitly
      understood + a retry/cleanup step exists). done when a failed run exits
      on its own within ~2 min instead of lingering 10–18 min.
- [ ] hiringcafe 403 root-caused (not necessarily fixed). done when a short
      note in the reasoning/issue states why `GET https://hiringcafe.com/`
      returns 403 and what the fix options are.
- [ ] A fresh delta report (new jobs / disappeared / top-30 with salary +
      sentiment) is presented to the human. done when `gold.new_this_run` /
      `gold.ranked_jobs` reflect the new run and the markdown tables are shown.

## Current state

- **Branch**: `main` (clean). Handoff doc is on a `handoff/<date>-<slug>`
  branch per the branch policy (repo protects `main`).
- **Pinned SHA**: `bcabcbc222e4b9cf19bebf7803f6655b3ffe6fbc`
- **Last completed step**: PR #49 merged — jd-refresh skill doc updated to the
  newer markdown-table reporting revision (same columns for new-jobs + top-30,
  apply_url suppressed, salary + sentiment + days_since_posted) AND a
  background/long-timeout note added so agents run the pipeline as a
  background job instead of a short blocking command. Verify via:
  `git log --oneline -1` → `bcabcbc docs(skill): jd-refresh markdown-table
  reporting + background-run timeout note (#49)`
- **Dirty/clean**: clean on `main` (nothing uncommitted). `data/` is gitignored
  and travels separately via R2.

## Next steps

1. Pin to the handed-off revision and verify the skill doc + repo state:
   `git fetch && git checkout bcabcbc222e4b9cf19bebf7803f6655b3ffe6fbc`, then
   `git log --oneline -1` → the PR #49 commit. Report PASS or discrepancy.
2. Reproduce the `ranked_csv` crash against the carried `data/` warehouse:
   `job-search-toolkit pipeline run --boards freework` (fast board) as a
   background/long-timeout job. Expected: `STEP_FAILURE` on `ranked_csv`,
   `TypeError: can only join an iterable` at
   `src/job_search_toolkit/pipelines/jd/assets/score.py:164`
   (`"|".join(job.get("technologies", []))` — `.get(key, [])` returns the
   default only for ABSENT keys, not for a present-`None` value; some active
   job row has `technologies = NULL`).
3. Fix `ranked_csv` to coerce `None` → `[]` (or filter) so the CSV export is
   robust to a `NULL`/missing `technologies`. done when the failing board run
   completes past `ranked_csv` (`grep "ranked_csv.*STEP_SUCCESS"`).
4. Run a full `job-search-toolkit pipeline run` in the background (all active
   boards) and confirm it reaches `RUN_SUCCESS`. If a step still fails, apply
   the same root-cause discipline (no symptom suppression).
5. Diagnose the post-`RUN_FAILURE` hang (observed BOTH runs this session: the
   dagster process stayed alive 10–18 min after `RUN_FAILURE` until manually
   cancelled). Look at how the CLI awaits the dagster job / whether a
   failure-path handle is leaked. done when a failed run self-exits promptly.
6. Investigate hiringcafe 403 separately (`hiringcafe.py::_fetch_build_id`,
   `GET hiringcafe.com/` → 403). Record root cause + options; do NOT hold the
   funnel on it — run other boards meanwhile.
7. Run a clean full refresh and report the delta per the (now-markdown-table)
   jd-refresh skill: new jobs, disappeared jobs, top-30 with salary +
   sentiment. End by presenting to the human; do not shortlist on their behalf.

## Key files

- `skills/jd-refresh/SKILL.md` — the playbook (just updated; follow it for
  delta reporting, incl. run pipeline in background/long-timeout).
- `src/job_search_toolkit/pipelines/jd/assets/score.py` — `ranked_csv` (line
  ~164) is the deterministic crash site.
- `src/job_search_toolkit/scrapers/hiringcafe.py` — `_fetch_build_id` 403 site.
- `src/job_search_toolkit/pipelines/jd/definitions.py` — `RANKING_BOARDS`,
  `full_pipeline` asset graph, `--boards` selection.
- `src/job_search_toolkit/run_config.py` + `config.yaml` (gitignored) — run
  mechanics (timeouts, max pages); search criteria live in
  `job_search_preferences.yaml` (gitignored).
- `data/warehouse/jobs.db` — DuckDB warehouse (silver.jobs + gold.* views);
  carried via R2. Gitignored, never commit.

## Decisions made & constraints discovered

- The jd-refresh skill must run the pipeline as a **background/long-timeout
  job** — a short blocking command kills the multi-board run mid-flight and
  surfaces a misleading truncated Dagster op failure. (Now written into the
  skill.)
- The newer markdown-table reporting revision was **ahead of the repo** (only
  in the installed plugin cache); it is now the committed source of truth in
  `skills/jd-refresh/SKILL.md`. If the box sees the plugin still lacks the
  timeout note, that is fixed only by a wheel rebuild/reinstall — treat as
  optional packaging follow-up, not a code defect.
- `dim_company.company_type` / `org_type`: `org_type` deprecated; use
  `company_type` (deterministic growth-stage proxy). Never fabricate a news
  sentiment; `NULL` = not enriched, `inconclusive` = valid researched state.
- Repo is PUBLIC. Personal data, salaries-of-self, target-company info, and
  API keys must never be committed. `data/`, `resume/`, `applications/`,
  `rendercv_output/` are gitignored. `data/` here travels only via R2 mirror.
- DuckDB `data/warehouse/jobs.db` is single-writer. Check for orphaned python
  processes holding it before a run (the hang above can leave one).
- No `/tmp` on the box's partner workflow convention — use repo-local gitignored
  scratch (`data/_tmp_*`) if scratch files are needed.
- API keys for enrichment come from the box's own `~/.omp/provider.env`
  (`DEEPSEEK_API_KEY` present). Do not copy `.env` or any secret into
  HANDOFF.md or commits.

## Open questions / risks / how to verify

- Why does the dagster process hang after `RUN_FAILURE`? Reproduce on a
  failing run and inspect the CLI's job await path. Verify by timing a failed
  run's process lifetime.
- Is hiringcafe.com blocking by user-agent/IP/geo, or did the site change?
  Probe with a browser UA from the box. Verify by re-fetching the homepage and
  reading the response.
- Will the box's scrape of hiringcafe also 403 (same egress network may share
  the block)? If so, run other boards and log hiringcafe as down.

## Data state

- **Read sources**: the 9 active board scrapers (freework, hellowork,
  englishjobs, faruse, linkedin_jobs, linkedin_posts, remoteok, wttj, wwr) via
  their live sites; plus the carried DuckDB warehouse. hiringcafe.com is
  currently 403-blocked (down at handoff).
- **Write targets**: `data/warehouse/jobs.db` (silver.jobs + dim tables +
  gold.* views, Kimball star); `data/bronze/{board}/…` + `runs.json` (immutable
  snapshots); `data/silver/*` bridge exports (jobs_ranked.csv, merged_jobs.json).
  Schema contract version = current silver DDL in
  `src/job_search_toolkit/pipelines/jd/silver.py`.
- **Invalidation blast radius**: changing the scoring formula invalidates
  cached scores in silver.jobs (self-versioned derivation). Changing
  `ranked_csv` output columns affects `jobs_ranked.csv` consumers (the
  new-application skill shortlist reads it). Gold views are `CREATE OR REPLACE`
  and rebuilt each run. No external dashboards/alerts consume the warehouse at
  handoff.
- **In-flight run state**: NONE mid-flight. The last run (this session,
  boards-minus-hiringcafe) reached `RUN_FAILURE` at `ranked_csv` after gold
  views had already materialized; the process was cancelled. The warehouse
  reflects that partial run (gold fresh as of the run). Fence: the box agent
  MAY write to `data/` targets; it owns the warehouse from here.
- **Verbatim data contracts**:
  - Jobs are never deactivated. `is_active` stays TRUE once seen; staleness is
    inferred from `last_seen_at` vs `STALE_AFTER_DAYS`. Subset (`--boards`)
    runs are safe.
  - `ranked_csv` bug: `job.get("technologies", [])` must handle a present-`None`
    value, not just an absent key. Treat missing/NULL `technologies` as an
    empty set for the CSV join.
  - Salary is a JSON dict `{min_annual_eur, max_annual_eur}`; display "not
    listed" when undisclosed (never 0/penalty).

## Data handoff (object storage)

- `data/` → `s3://agent-handoff/handoff/job-search-toolkit/data`
  (target: `~/repos/job-search-toolkit/data`) — mirrored via R2 `r2-handoff`
  profile, boto3 on both ends. Source (laptop `data/`) is authoritative.

## Explicitly NO

- Secrets/keys/connection strings — reference by logical name only
  (enrichment key lives in the box's own `~/.omp/provider.env`).
- Absolute paths/hostnames/usernames; repo-relative only.
- Sample data rows / transcripts / log dumps.
