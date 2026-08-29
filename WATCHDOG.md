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

## Subagent LLM-provider credit wall (HIGH — WS5 2026-08-29)

Subagents run on a different provider than the main harness
(`openrouter/z-ai/glm-5.3-flash`). When that provider is out of credits,
subagents fail in ~250ms with `402 ... exceed your available credits ...`, doing
nothing — not a code regression. Watch for the 402 signature before attributing
a failed subagent to the work itself. If the provider is down, right-size to an
inline orchestrator fix for a small, fully-specified change rather than
re-dispatching into a credit wall.

## Report / test-helper schema trap (WS5 2026-08-29)

`silver.jobs` is PK `(id, source_board)` and `ensure_jobs_table` derives its DDL
from the incoming job-dict keys, then ALWAYS appends `company_id` itself. A test
helper that passes a `company_id` key in the job dicts it hands to
`ensure_jobs_table` raises DuckDB `Catalog Error: Column with name company_id
already exists!`. Build the schema from `company_id`-free dicts, then insert
rows that carry it.


## WS7 (2026-08-29) — edit-tool mangle variants + "no producer" gap

The edit-tool mangle recurred on 5 files this session. New observed variants
beyond the WS1 constant-drop:
- **Append-after-function dropped the function's own tail** (score_engine.py:
  appending the lead section after `return jobs` dropped `return jobs`; later
  appending `score_leads_from_warehouse` after `lead_apply_calibration`
  dropped that function's two `raise` lines).
- **Add-command dropped the PRIOR function's tail + duplicated a line**
  (cli.py `bd leads` lost its print loop; `app.add_typer(bd_app...)` doubled).
- **Tuple edit dropped a tuple member's tail + duplicated a closing paren**
  (gold.py `_BD_VIEWS` relationship view).
- **Markdown edit dropped the header + duplicated the block** (workstreams.md).
All parse fine (ast.parse is useless here); only grep/re-read of the anchor
region + a behavioral smoke catches them. When the edit tool says "operation
needs »" / "no change" / fuzzy no-marker replace, re-read — those are the
mangle signals. For structural rewrites use full-file write or a fail-loud
line-indexed python splice.

## WS7 (2026-08-29) — "second consumer / gold view" must have a producer

Implementing a gold view over new tables without wiring a producer that
POPULATES the underlying table leaves it a dead end. Lead scoring shipped
engine + views + write helper but nothing called `upsert_lead_scores` —
`bd leads` returned none, `pipeline lead-score-report` showed 0. A reviewer
caught it. Reviewer must check: does any src/ path write the table the new
view reads? Is the full path (source tables -> output table -> view) exercised
by a test, not just the pure function on hand-built dicts?

## WS3 (2026-08-29) — tailoring quality: deterministic verify + reviewer guard-ceiling

- `verify_pdf` (automation/tailor/verify.py) is DETERMINISTIC — pure pypdf text-layer
  checks, no LLM. A reviewer should reject any attempt to make verification LLM-based
  or to keyword-stuff (honesty rule: genuine gaps are reported, never stuffed). Contact
  matching normalizes curly apostrophes and filters the cv block to string
  `{name,email,phone,location}` — it must not crash on nested master fields
  (social_networks/sections).
- The drafter-reviewer (`automation/tailor/reviewer.py`, `tailor run --with-review`) is
  the ONLY post-draft LLM call; `bounded_revise` runs EXACTLY one revise pass, and
  `apply_revision` re-runs `check_fabrication` so the guard is the ceiling — a
  reviewer-proposed unsupported skill/metric must be rejected with the first pass left
  intact (verify the guard, not the reviewer's self-report).
- Contract-test fixtures must use FICTIONAL PII (example.com email, 555 phone) — the
  pre-push hook blocks real emails/phones in this public repo (see tasks/lessons.md
  2026-08-29). Any new test that embeds an email-like string must allowlist the
  synthetic value in scripts/hooks/pre-push.
- Default single-pass `tailor run` (no `--verify`/`--with-review`) must stay
  byte-identical; verify/reviewer are strictly opt-in flags.
