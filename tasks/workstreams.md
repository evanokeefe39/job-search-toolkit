# Workstreams

Index of the toolkit's work streams (from the expert-panel assessment, 2026-08-28).
Each work stream lists its epics and the plan docs that specify them. Detailed plans
live in `tasks/plans/`; this file is the durable summary and current-status record.

**Status legend:** `active` = current focus · `deferred` = parked, revisit later ·
`roadmap` = explicitly out of scope for now (vision only).

## WS1 — Outcome Loop & Tracking Spine *(the differentiator; issue #30)*
- **Epic 1.1** Pluggable tracker interface (append-only outcome event feed)
- **Epic 1.2** Outcome feedback into warehouse + `score_engine` (gold.score_calibration, --apply-calibration)
- **Epic 1.3** Twenty adapter (T2; backend swap, not migration)
- **Status:** `implemented 2026-08-29` — Epics 1.1/1.2/1.3 landed (tracker interface + SQLite backend + CLI; outcome feedback via silver.fact_outcome_event + gold.score_calibration + score-report/--apply-calibration; Twenty adapter); bit-for-bit + contract tests green, pipeline gold + pipeline run smoke pass. WS5/WS7 consume this contract next.
- **Plans:** `tasks/plans/ws1-outcome-loop.md`

## WS2 — Discovery: ATS Providers & Community Contract
- **Epic 2.1** ATS API providers (Greenhouse / Ashby / Lever; skip Workday)
- **Epic 2.2** Providers abstraction (community `Provider` contract + source-indexing policy)
- **Epic 2.3** Board registry + region onboarding
- **Status:** `deferred` (parked 2026-08-28; revisit after WS1/WS7 land)

## WS3 — Tailoring Quality
- **Epic 3.1** Rendered-PDF / ATS text-layer verification
- **Epic 3.2** Adversarial drafter-reviewer (bounded single revise loop)
- **Status:** `implemented 2026-08-29` — Epics 3.1/3.2 landed on
  `feat/ws3-tailoring-quality`: `tailor verify` (or `tailor run --verify`)
  extracts the rendered `cv_tailored.pdf` text layer with pypdf and asserts
  contact-as-literal-text (apostrophe-normalized), no mojibake/PUA glyphs,
  section reading order, page count vs `verify_page_target`
  (`tailor_resume_preferences.yaml`), and JD keyword coverage in
  covered/supported-missing/genuine-gap buckets (honesty rule); a FAIL
  blocks the `ready` transition (skill gate updated). Epic 3.2 adds the
  bounded single-revise drafter-reviewer behind `tailor run --with-review`:
  a fresh-context reviewer critiques the first pass against master + JD +
  verification report and proposes ONE targeted revision; the fabrication
  guard (`automation/tailor/audit.py`) is the ceiling — an unsupported
  claim is rejected and the first pass stays intact. Both flags are opt-in;
  the default single-pass `tailor run` is byte-identical. Contract tests
  written first (`tests/test_verify.py` + `tests/test_reviewer.py`); full
  suite 410 passed. **pypdf is the only new dependency.**
- **Plans:** `tasks/plans/ws3-tailoring-quality.md`

## WS4 — Market Insights
- **Epic 4.1** Skill taxonomy (canonical alias map)
- **Epic 4.2** Skill/tech trending (`gold.skill_trends` + market-trends skill)
- **Status:** `active` (opportunistic parallel track)
- **Plans:** `tasks/plans/ws4-market-insights.md`

- **Status:** `implemented 2026-08-29` — Epics 5.1–5.3 landed (per-folder append-only
  `status.yaml` + `application record` that sinks transitions into the warehouse via the WS1
  tracker interface; follow-ups-due query + drafts-only cap-2 follow-ups; deterministic per-job
  report with explicit "unknown" gaps; new `application` CLI group + record-outcome/follow-up
  skills; application-tracker/new-application record via `application record`). 26 tests green,
  `pipeline gold` + `pipeline run` smoke pass. Epic 5.4 (Gmail/reply-watch) is DEFERRED until ~20
  real manually-recorded outcomes exist — noted in the plan, not implemented. WS7 consumes WS1's
  tracker contract next.
- **Plans:** `tasks/plans/ws5-application-workflow.md`

## WS6 — Productization & Growth
- **Epic 6.1** README repositioning (one-liner, differentiators, personas, tiers, honest comparison)
- **Epic 6.2** Distribution basics (external data dir + `uv tool install`)
- **Epic 6.3** Apify actors *(in-repo under `apify_actors/` for now; ≤2 actors, ≤20% dev time; copied out later)*
- **Status:** `active` (6.1 near-term; 6.3 parallel side-income track)
- **Plans:** `tasks/plans/ws6-productization-growth.md`

## WS7 — BD/CRM Depth (T3)
- **Epic 7.1** BD dimensions in the same warehouse (`dim_person`, `fact_touch`, `fact_referral`, `fact_inbound_attribution`)
- **Epic 7.2** Lead scoring (`score_engine` second consumer over companies/contacts)
- **Status:** `implemented 2026-08-29` — Epics 7.1/7.2 landed on `feat/ws7-bd-depth` (PR to follow): append-only BD dim/fact tables + gold BD views (contact_cadence, referral_funnel, inbound_conversion, event_funnel, next_action, relationship), outreach_tracker.csv backfill, `bd` CLI group + warehouse-backed cold-outreach skill (7.1); deterministic zero-LLM `score_leads` as score_engine second consumer with versioned lead weights + validated sum-to-1.0, `gold.lead_rank`/`gold.lead_score_calibration`, gated lead calibration reusing WS1 versioned-config + active-override machinery (never LLM weights), `bd score-leads`/`bd leads` + `pipeline lead-score-report`, and a route-by-score step in the skill (7.2). 28 WS7 contract tests + bit-for-bit job-score regression + spine regression green; pipeline gold + pipeline run --boards freework smoke pass. WS1/WS5 contracts untouched.
- **Plans:** `tasks/plans/bd-warehouse-dimensions.md` (7.1), `tasks/plans/lead-scoring.md` (7.2)

## WS8 — Deferred / Roadmap
- Interview prep + STAR bank (lazy from real interviews)
- Cover letters (when a role requires one)
- Scam/ghost-job detection (opportunistic deterministic gold view)
- Offer/negotiation scripts (when offers arrive)
- Student-coach GTM program (README vision only)
- Full BD playbooks (only once T1 funnel data shows actual leaks)

## Execution mode (decision, 2026-08-28)

Workstreams are executed **serially**, not via the parallel plan. The parallel plan (spine refactor,
contracts doc, integration-owner machinery) was designed for concurrent writers and is dead weight
with one writer at a time.

- **Do NOT** run the parallel operating model: no `integrate/waves` branches, no integration owner,
  no frozen-contracts-before-spawn, no per-merge smoke waves.
- **DO** write each stream's contract tests as part of that stream — above all the bit-for-bit golden
  score fixture (protects "the ranking doesn't silently shift") — and do the `score_engine` weight-config
  extraction (`scoring_config`) + dead `main()`/argparse removal inside WS1.
- **DEFER** the gold-registry / silver-DDL facade split unless gold.py growth or a future parallel push
  justifies it. Append new views/DDL directly while serial.
- **Order:** WS1 first (defines the tracker interface, owns score config) → WS5/WS7 (consume WS1's
  contracts) → WS3/WS4/WS6 any time (disjoint files). This is already each plan's natural dependency order.
- If parallel execution is later pursued, revisit the Wave-0 spine refactor + `tasks/contracts.md` then.