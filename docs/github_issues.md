# GitHub Issues — local mirror (for grepping)

Mirror of the remote GitHub issues so their content is greppable locally. The
source of truth remains github.com/evanokeefe39/job-search-toolkit/issues;
regenerate with `gh issue list --state all --limit 100` + `gh issue view N --json body,comments`.
Generated: 2026-08-29.

**Count:** 10 issues — 8 open, 2 closed.

---

## Open

## #19 [OPEN] Feedback/learning funnel: consume outcomes to close the loop

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/19
- **Author:** evanokeefe39 · **Created:** 2026-08-27T12:37:52Z
- **Labels:** audit

**From** docs/cross_domain_audit.md — the single highest-value gap, flagged by every expert.

The pipeline produces applications but consumes no outcomes. Everything is front-loaded (scrape → enrich → score → tailor → ready); nothing feeds applied → interview → offer → rejected → ghosted back into scoring, tailoring, or outreach. All current tracker rows sit at ready/tailoring, zero applied.

**DoD**
- [ ] Add an outcome/feedback schema (or extend Application) that captures per-role results after submission.
- [ ] application-tracker writes real outcomes as they happen.
- [ ] Scoring engine reads outcomes to retune weights.
- [ ] A summary surface reports response rate by source and stage, benchmarked against the 10-15% norm.

---

## #20 [OPEN] Scoring dimensions are preference-weighted, not outcome-weighted

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/20
- **Author:** evanokeefe39 · **Created:** 2026-08-27T12:37:57Z
- **Labels:** audit

**From** docs/cross_domain_audit.md — data-engineering lens.

The five scoring dimensions (pay 0.30, flexibility 0.25, low-responsibility 0.20, tech-match 0.15, company-quality 0.10) encode preference, not what predicts an interview/offer. No ground-truth labels feed back to validate or retune them.

**DoD**
- [ ] Capture ground-truth outcome labels from real applications (see feedback-funnel issue).
- [ ] Compare predicted rank vs actual outcome; report a calibration/validation metric.
- [ ] Document whether weights stay preference-based or shift to outcome-based (likely a hybrid: preferences for selection, outcomes for prioritization).

---

## #21 [OPEN] Close discipline: pipeline stops at 'ready', applications never sent

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/21
- **Author:** evanokeefe39 · **Created:** 2026-08-27T12:38:04Z
- **Labels:** audit

**From** docs/cross_domain_audit.md — sales/BD + recruiter lenses.

The ready pile is a trap: 12 applications tailored, 0 sent. The tool became the end. Delivery problem, not quality. Re-read TechTalk's 'apply to 3/day' as *you are not sending enough*, not *spray indiscriminately*.

**DoD**
- [ ] Add a weekly cadence/ritual that moves ready → applied (see cadence issue).
- [ ] Surface 'ready but unsent' as an explicit queue with age.
- [ ] Optionally a volume target/guardrail (e.g. N sent/week).

---

## #22 [OPEN] Lead scoring disconnected from outreach routing

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/22
- **Author:** evanokeefe39 · **Created:** 2026-08-27T12:38:06Z
- **Labels:** audit

**From** docs/cross_domain_audit.md — sales/BD lens.

Lead-scoring (Intent × Fit × (Access+Urgency)/2) is strong on paper but nothing routes leads into outreach by score. Scoring and outreach are two systems that never meet.

**DoD**
- [ ] After scoring, classify hot/warm/cold (thresholds e.g. >0.7 / 0.4-0.7 / <0.4).
- [ ] Hot leads feed the cold-outreach (or relationship) playbook automatically.
- [ ] Every Opportunity gets 'next action + by when' derived from its tier.

---

## #23 [OPEN] Add Access signal to lead scoring

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/23
- **Author:** evanokeefe39 · **Created:** 2026-08-27T12:38:12Z
- **Labels:** audit

**From** docs/cross_domain_audit.md — sales/BD lens.

Access ('can I actually reach a decision-maker?') is in the composite formula but underweighted/underexercised. A perfect-fit company you cannot reach is worth less than a decent fit with a warm path.

**DoD**
- [ ] Make Access a first-class, populated signal (known contacts, recruiter network, warm-intro path), not a default.
- [ ] Surface 'why we can/can't reach this company' when triaging hot leads.

---

## #24 [OPEN] Weekly operational cadence / ritual (SOP)

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/24
- **Author:** evanokeefe39 · **Created:** 2026-08-27T12:38:13Z
- **Labels:** audit

**From** docs/cross_domain_audit.md — operations/SOP lens.

Every skill is reactive ('when asked'). Nothing schedules a rhythm: weekly jd-refresh, monthly market review, outreach follow-up sweep. Phase 5 analytics can't run without a trigger.

**DoD**
- [ ] Define a weekly ritual: refresh → triage → follow-up → review.
- [ ] Add a way to surface 'due now' actions (open ready-unsent, untouched outreach contacts >7 days, applications awaiting response).
- [ ] Optionally wire to application-tracker stats + cold-outreach follow-up.

---

## #25 [OPEN] Instrument inbound/content funnel

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/25
- **Author:** evanokeefe39 · **Created:** 2026-08-27T12:38:20Z
- **Labels:** audit

**From** docs/cross_domain_audit.md — product/PM lens.

outreach_tracker has a direction: inbound field never used. Inbound leads convert 5-10x cold (portfolio, market-research reports, content) but there is no attribution or tracking.

**DoD**
- [ ] Populate direction: inbound and the source asset when inbound occurs.
- [ ] Add attribution: which asset produced which inbound lead.
- [ ] Report inbound vs outbound conversion separately (Phase 5 lead-source attribution).

---

## #26 [OPEN] Split cold-outreach into distinct playbooks (role vs relationship vs service)

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/26
- **Author:** evanokeefe39 · **Created:** 2026-08-27T12:38:21Z
- **Labels:** audit

**From** docs/cross_domain_audit.md — companion section.

Cold outreach is not one activity. Split into role-anchored, relationship-building, service/solution, warm/inbound, event/context — each with its own SOP, message style, cadence, CRM tag, and metric. CRM distinction: Contact (Person, durable) vs Touch (Outreach message, multi-row per person with playbook + touch_number).

**DoD**
- [ ] Extend outreach data model: playbook + touch_number on Outreach; Person as durable.
- [ ] One-page SOP per playbook under skills/.
- [ ] Follow-up cadence query ('any Person untouched in 7+ days?').
- [ ] Relation-tracker (recruiter relationships) as a distinct track (see crm issue).

---

## Closed

## #30 [CLOSED] Outcome feedback loop must be a Tier-1 data feature, not gated behind Tier 3

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/30
- **Author:** evanokeefe39 · **Created:** 2026-08-28T14:28:46Z  ·  **Closed:** 2026-08-29T12:09:30Z
- **Labels:** (none)

### Context

`docs/cross_domain_audit.md` (2026-08-27) through-line: **the system produces applications but does not consume outcomes** — no return leg feeds `applied -> interview -> offer -> rejected -> ghosted` back into scoring/tailoring/outreach. Tracked by existing issues #19 (feedback loop), #20 (outcome-weighted scoring), #21 (close discipline).

### The new finding (from design review of readme_plan.md tiers)

`docs/readme_plan.md` defines the opt-in tiers as:
- **T1** — core, "track in a CSV", no CRM
- **T2** — adds Twenty as system of record (Opportunity, ATS score, response-rate stats)
- **T3** — full BD: outreach playbooks, lead scoring/routing, referral, inbound attribution, weekly ritual

The tiers correctly gate **BD breadth** (referral / inbound / event / follow-up cadence / outreach routing). But they **must not gate the feedback loop itself**, which is a **data-layer concern cheapest and most tractable at T1**: the DuckDB warehouse + a tracker recording outcomes is exactly the ground-truth-label pipeline for outcome-weighted `score_engine` retuning (#20's cleanest fix).

### Tier × audit-issue coverage

How each tier covers the cross-domain issues from `cross_domain_audit.md` (`✓` = covers, `~` = partial, `—` = open). Tiers gate the BD/relationship/workflow layer; the data feedback loop is a Tier-1 concern no tier fully closes alone.

| Audit issue | **T1** (core, basic tracker) | **T2** (+ Twenty) | **T3** (+ BD) |
|---|---|---|---|
| Outcome feedback loop / "consume outcomes" (through-line) | **FOUNDATION** — SQLite tracker + DuckDB warehouse = the label pipeline, *if wired* | richer history + response-rate stats | more data + workflow discipline, but retuning still separate |
| Ready-pile trap / close discipline | ~ lower friction to record (helps), no push | ~ app-centric, no push | ✓ apply + outreach discipline |
| Follow-up cadence | — | — | ✓ |
| Company-side follow-up (recruiter) | — | ~ Twenty holds recruiter records | ✓ |
| Per-relationship vs per-message tracking | — | ~ Twenty Person/Outreach model | ✓ Person/Touch |
| Lead scoring → routing | — | ~ lead-scoring model exists | ✓ routing |
| Outcome-weighted scoring / ground-truth labels (DE) | **✓ if built here** — cleanest, cheapest, pure DE | ~ | ~ |
| Referral funnel | — | — | ✓ |
| Inbound/content funnel | — | ~ `direction:inbound` exists | ✓ attribution |
| Event/community funnel | — | ~ Twenty `Event` model | ✓ cadence |
| Time/effort funnel | ~ | ~ | ~ none fully |
| Ops cadence / weekly ritual | — | — | ✓ |
| Product / student-coach program | — | ~ default path (readme_plan) | ~ separate program |
| Security (student-data isolation) | — | — | — orthogonal to tier |

#### What this reveals

1. **The through-line fix is a Tier-1 decision, not a Tier-3 reward.** Outcome-weighted deterministic scoring from ground-truth labels lives entirely in the data layer — Tier 1 already has the DuckDB warehouse, and with a basic tracker recording outcomes it is where `applied -> interview -> offer -> rejected` labels feed `score_engine`. Do not gate the loop behind the tiers; build it at T1.
2. **Risk: low-tier users get the audit's exact pathology.** If T1 is CSV-only with no outcome recording wired to scoring, T1 users hit the ready-pile trap (produce, don't consume). The feedback loop should be the **default at T1**, not a premium at T3. Strongest argument for SQLite-over-CSV at the basic tier: CSV tracking with no scoring hook preserves the trap.
3. **Two issues no tier closes by itself.** Student-coach (#13 / program layer — readme_plan flags a separate `STUDENTS.md` path defaulting to T2) and Security (#14 — student data isolation must be designed *before* any student data exists, orthogonal to tier).

### Risk

If T1 is implemented as "CSV only, outcome recording not wired to scoring," T1 users get precisely the audit's pathology — the funnel stops at `ready`, no labels ever reach the warehouse. The tiering would preserve the ready-pile trap for the lowest-effort users instead of fixing it.

### Related: Twenty is currently ASSUMED, not opt-in

`new-application`, `tailor-resume`, and `application-tracker` hard-code the sibling `../crm` `crm-bridge sync` call, which requires the private `../crm` repo **and** a running Twenty (`http://localhost:3000`, Docker). A public-repo clone has neither. Plan: a backend-agnostic `tracker` interface (basic = local SQLite; twenty = existing bridge), backend selected by config.

### Proposed direction (do NOT implement yet — documenting only)

- Pluggable tracker backend (basic SQLite / twenty), reusing the `data_model.md` Application schema + status vocabulary.
- **Non-negotiable:** even the basic tier writes outcomes into the warehouse, so the feedback loop and outcome-weighted scoring are live from T1; tiers add BD breadth on top, not the loop.
- Migration path basic -> twenty (lossless export) so opting up is a config change.
- This issue is deliberately separate from the not-yet-written `tracker-backends` plan.

**Comments (1):**

- **evanokeefe39** (2026-08-29T12:09:29Z):

  Resolved by WS1 (PR #31): the outcome feedback loop is now a Tier-1 data feature — the tracker (append-only event feed, SQLite default, Twenty config-swap) writes outcomes via `job-search-toolkit tracker record`, the `warehouse_outcomes` asset lands them in `silver.fact_outcome_event`, `gold.score_calibration` + `pipeline score-report`/gated `--apply-calibration` retune `score_engine` from ground-truth labels (SQL-evidenced, never LLM-proposed). The tiering no longer gates the loop; T1 ships with SQLite tracking wired to scoring. Skills no longer hardcode `../crm`/Twenty.

---

## #34 [CLOSED] WS3 — Tailoring Quality: ATS text-layer verification + adversarial drafter-reviewer

- **URL:** https://github.com/evanokeefe39/job-search-toolkit/issues/34
- **Author:** evanokeefe39 · **Created:** 2026-08-29T13:58:08Z  ·  **Closed:** 2026-08-29T14:51:17Z
- **Labels:** (none)

Tailoring-quality workstream (WS3, `tasks/plans/ws3-tailoring-quality.md`). Serial, after WS1/WS5/WS7.

### Epic 3.1 — Rendered-PDF / ATS text-layer verification
`tailor verify` (or a `--verify` stage in `tailor run`) extracts `cv_tailored.pdf` text with **pypdf** and asserts:
- contact info (name, email, phone, location) present as literal text (no icon glyphs / missing codepoints / U+FFFD mojibake)
- sane reading order (name → contact → experience → skills)
- page count (target 2, overridable in `tailor_resume_preferences.yaml`)
- keyword coverage bucketed into covered / supported-but-missing / genuine-gap (honesty rule — never keyword-stuff)
Verification failure blocks the "ready" transition.

### Epic 3.2 — Adversarial drafter-reviewer (bounded single revise loop)
`tailor run --with-review`: fresh-context reviewer critiques first-pass `cv_tailored.yaml` against JD + master resume + verification report, returns a critique + ONE targeted revision; exactly one revise pass then re-verify; "no changes" is a no-op; iteration capped. The fabrication guard (`automation/tailor/audit.py`) is the ceiling — a reviewer-proposed unsupported claim is rejected.

### Constant value
- `job-search-toolkit pipeline run` / zero-LLM ranking path, WS1 tracker, WS5 status.yaml/application CLI + skills, and the **DEFAULT single-pass `tailor run`** (no `--verify`/`--with-review`) must stay byte-identical. Flags are opt-in.

### DoD
- All behavioral contracts + edge cases tested (contract tests FIRST).
- Default path + pipeline + WS1/WS5 still work end-to-end.
- Linter clean; **pypdf is the only new dependency** (noted).
- Reasoning trace + assumption log; `tasks/workstreams.md` updated.
- PR merged.

**Comments (1):**

- **evanokeefe39** (2026-08-29T14:01:44Z):

  In progress (2026-08-29): contract tests written first (tests/test_verify.py — 8 tests incl. contact-literal/mojibake/reading-order/page-count/keyword-buckets/no-text-layer; tests/test_reviewer.py — 4 tests incl. bounded loop + guard-ceiling), currently red against unimplemented modules. Epic 3.1 verify module + Epic 3.2 reviewer module dispatched in parallel (disjoint files). pypdf added as the only new dependency. Integration (tailor verify + --verify/--with-review flags, skill gate) next.

---
