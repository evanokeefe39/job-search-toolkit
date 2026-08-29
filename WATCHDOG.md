# WATCHDOG.md — advisor review guidance (WS1 outcome loop)

Advisor-only: risks a reviewer/advisor should watch for on this repo. Terse and
concrete — specific failure modes, not generic advice. Not a spec.

## edit-tool mangle hazard (HIGH — recurred repeatedly in WS1)

The `edit` tool has silently dropped/corrupted code on this machine multiple
times. In WS1 (2026-08-29) an edit dropped the module constant `_STALE` from
`gold.py` — the file still compiled, and the break surfaced only when
`pipeline gold` ran. A subagent rewrite of `latest_run` also pulled its body
into a new function.

- **Any multi-line `edit` on Python → re-read the exact region afterward.**
- **A compile passing is NOT sufficient** — the mangle was a silent deletion of
  a used symbol. `pipeline gold` / a smoke that executes the module is the real
  check.
- After one mangle on a file, switch to full-file `write` (repo lessons rule).
- Watch for: a `diff` that removes a used constant while adding a feature block
  in the same hunk — the classic "added CALIBRATION block, dropped `_STALE`"
  pattern.

## Subagent runtime cap (HIGH)

`task.maxRuntimeMs=600000` (10 min) is far too short for substantial
implementations on this repo, because `import dagster` is ~180 s cold. Subagents
regularly hit the cap mid-work: their edits land but are **unverified and often
incomplete/broken** (tests unrun, a dropped constant, a fixture that mirrors a
code bug). A subagent "completed with code written" is NOT done — the
orchestrator must verify by running the module (smoke/gold) and the tests.

- Never trust a subagent's self-report that its code works; the WS1 agents
  aborted with "code written" while `pipeline gold` was broken.
- Split work so each subagent can verify within 10 min, or plan to finish the
  tail yourself after the cap aborts it.

## dagster cold import is ~180 s (environment)

`import dagster` takes ~3 min cold on this machine; the full suite swings
between ~40 s (warm) and ~8 min (cold). Do not read slowness as a WS1
regression. Warm the cache before timing a suite. `uv run python -c "import
dagster"` once to warm it.

## DuckDB single-writer (Windows)

The warehouse `data/warehouse/jobs.db` is single-writer. A leftover reader (a
stray pytest/analysis process, DBeaver) makes any `connect()` hang. `timeout` on
git-bash does NOT kill child python — it leaves orphans that hold the lock.
Check `ps aux` for `pytest`/`python` holding the repo before diagnosing a hang
as a code regression, and kill orphans (they accumulate and slow everything).

## Calibration (score-report / --apply-calibration)

- Weights change ONLY via `pipeline score-report --apply-calibration` with SQL
  evidence; never LLM-proposed. A reviewer should reject any code that lets an
  LLM write weights.
- Per-feature gating: a feature is adjusted only when its HIGH band has
  >= `MIN_COUNT` applied outcomes; ineligible features get delta 0; no eligible
  feature -> "not enough data" refusal. This is deliberate, not a bug.
- The active-override file (`data/scoring_active.yaml`, env
  `JST_ACTIVE_WEIGHTS_FILE`) takes precedence over the bundled
  `scoring_config.yaml`; versions live in `data/scoring_config.versions/`. The
  bit-for-bit default contract (`test_job_score_bit_for_bit`) guards the
  bundled default — never let a calibration path alter it.

## Tracker invariants

- `silver.fact_outcome_event` is append-only and idempotent (UNIQUE on
  job_id/stage/ts/COALESCE(note,'')/provenance). `job_id` is deliberately a
  nullable join to `silver.jobs.id` (an outcome for an application folder with
  no warehouse job is still recorded).
- The tracker stage vocabulary is fixed (`discovered..ghosted`); unknown stages
  raise. Skills must call the tracker CLI (`job-search-toolkit tracker ...`),
  never hardcode `../crm` / `crm-bridge`.
- At T2 (`tracker.backend: twenty`) Twenty is authoritative; the SQLite store is
  a best-effort mirror, never a fork that overrides iter_outcomes().
