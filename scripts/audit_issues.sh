#!/usr/bin/env bash
# Create GitHub issues for each cross-domain audit gap.
set -euo pipefail

TK="evanokeefe39/job-search-toolkit"
CRM="evanokeefe39/crm"
AUDIT="docs/cross_domain_audit.md"

mk() { # repo, title, body
  local repo="$1" title="$2" body="$3"
  gh issue create --repo "$repo" --title "$title" --body "$body" --label audit
  echo "  created: [$repo] $title"
}

echo "== job-search-toolkit issues =="

mk "$TK" \
  "Feedback/learning funnel: consume outcomes to close the loop" \
"**From** $AUDIT — the single highest-value gap, flagged by every expert.

The pipeline produces applications but consumes no outcomes. Everything is
front-loaded (scrape → enrich → score → tailor → ready); nothing feeds
applied → interview → offer → rejected → ghosted back into scoring, tailoring,
or outreach. All current tracker rows sit at ready/tailoring, zero applied.

**DoD**
- [ ] Add an outcome/feedback schema (or extend `Application`) that captures
      per-role results after submission.
- [ ] `application-tracker` writes real outcomes as they happen.
- [ ] Scoring engine reads outcomes to retune weights (link #scoring-validation).
- [ ] A summary surface (e.g. gold view / report) shows response rate by source
      and stage, benchmarked against the 10-15% norm."

mk "$TK" \
  "Scoring dimensions are preference-weighted, not outcome-weighted" \
"**From** $AUDIT — data-engineering lens.

The five scoring dimensions (pay 0.30, flexibility 0.25, low-responsibility
0.20, tech-match 0.15, company-quality 0.10) encode *preference*, not *what
predicts an interview/offer*. There are no ground-truth labels feeding back to
validate or retune them.

**DoD**
- [ ] Capture ground-truth outcome labels from real applications (see feedback-funnel issue).
- [ ] Compare predicted rank vs actual outcome; report a calibration/validation metric.
- [ ] Document whether weights stay preference-based or shift to outcome-based
      (likely a hybrid: preferences for selection, outcomes for prioritization)."

mk "$TK" \
  "Close discipline: pipeline stops at 'ready', applications never sent" \
"**From** $AUDIT — sales/BD + recruiter lenses.

The `ready` pile is a trap: 12 applications tailored, 0 sent. The tool became
the end. This is a delivery problem, not a quality problem — re-read TechTalk's
'apply to 3/day' as *you are not sending enough*, not *spray indiscriminately*.

**DoD**
- [ ] Add a weekly cadence/ritual that moves ready → applied (see cadence issue).
- [ ] Surface 'ready but unsent' as an explicit queue with age.
- [ ] Optionally a volume target/guardrail (e.g. N sent/week) so delivery keeps pace with tailoring."

mk "$TK" \
  "Lead scoring disconnected from outreach routing" \
"**From** $AUDIT — sales/BD lens.

Lead-scoring (Intent × Fit × (Access+Urgency)/2) is strong on paper but nothing
routes leads into outreach by score. Scoring and outreach are two systems that
never meet.

**DoD**
- [ ] After scoring, classify leads hot/warm/cold (thresholds e.g. >0.7 / 0.4-0.7 / <0.4).
- [ ] Hot leads feed the cold-outreach (or relationship) playbook automatically.
- [ ] Every Opportunity gets 'next action + by when' derived from its tier."

mk "$TK" \
  "Add Access signal to lead scoring" \
"**From** $AUDIT — sales/BD lens.

Access ('can I actually reach a decision-maker?') is part of the composite
formula but underweighted/underexercised. A perfect-fit company you cannot
reach is worth less than a decent fit with a warm path.

**DoD**
- [ ] Make Access a first-class, populated signal (known contacts, recruiter
      network, warm-intro path), not a default.
- [ ] Surface 'why we can/can't reach this company' when triaging hot leads."

mk "$TK" \
  "Weekly operational cadence / ritual (SOP)" \
"**From** $AUDIT — operations/SOP lens.

Every skill is reactive ('when asked'). Nothing schedules a rhythm: weekly
jd-refresh, monthly market review, outreach follow-up sweep. Phase 5 analytics
can't run without a trigger.

**DoD**
- [ ] Define a weekly ritual: refresh → triage → follow-up → review.
- [ ] Add a way to surface 'due now' actions (open ready-unsent, untouched
      outreach contacts >7 days, applications awaiting response).
- [ ] Optionally wire to the application-tracker stats + cold-outreach follow-up."

mk "$TK" \
  "Instrument inbound/content funnel" \
"**From** $AUDIT — product/PM lens.

`outreach_tracker` has a `direction: inbound` field never used. Inbound leads
convert 5-10x cold (portfolio, market-research reports, content as lead
magnets) but there is no attribution or tracking.

**DoD**
- [ ] Populate `direction: inbound` and the source asset when inbound occurs.
- [ ] Add attribution: which asset produced which inbound lead.
- [ ] Report inbound vs outbound conversion separately (Phase 5 lead-source attribution)."

mk "$TK" \
  "Split cold-outreach into distinct playbooks (role vs relationship vs service)" \
"**From** $AUDIT — companion section.

Cold outreach is not one activity. Split into role-anchored, relationship-
building, service/solution, warm/inbound, event/context — each with its own
SOP, message style, cadence, CRM tag, and metric. CRM distinction: Contact
(Person, durable) vs Touch (Outreach message, multi-row per person with
`playbook` + `touch_number`).

**DoD**
- [ ] Extend outreach data model: `playbook` + `touch_number` on Outreach; Person as durable.
- [ ] One-page SOP per playbook under `skills/`.
- [ ] Follow-up cadence query ('any Person untouched in 7+ days?').
- [ ] Relation-tracker (recruiter relationships) as a distinct track (see crm issue)."

mk "$CRM" \
  "Track referrals as a distinct funnel" \
"**From** $AUDIT — referral funnel (blindspot #2).

A referral is a qualified warm intro that skips the application funnel. Not
currently tracked as its own pipeline, distinct from cold outreach and
relationship-building.

**DoD**
- [ ] Model referral as a first-class signal on Person/Opportunity
      (source: warm intro, by whom, when).
- [ ] Track 'has this relationship produced a referral?' and referral → application
      conversion.
- [ ] Surface referral-sourced applications separately in funnel stats."

mk "$CRM" \
  "Model Contact vs Touch: per-relationship outreach history" \
"**From** $AUDIT — sales/BD lens + companion section.

`Outreach` is one message per row keyed to a person; a Person with 3 touches
looks like 3 unrelated rows. Real CRM separates the durable Contact from each
Touch so relationship history accumulates.

**DoD**
- [ ] Person is the durable entity (persistent, accumulates history).
- [ ] Outreach is multi-row per person with `playbook` + `touch_number` + dates.
- [ ] Follow-up cadence is a query (untouched-in-7-days), not guesswork.
- [ ] Wire via crm-bridge once the object model supports it."

mk "$CRM" \
  "Follow-up cadence + 'next action by when' on every Opportunity" \
"**From** $AUDIT — sales/BD + recruiter lenses.

Cold outreach and applications have no scheduled follow-up. A warm lead or an
applied application that goes quiet falls through the cracks.

**DoD**
- [ ] Add next-action + due-date to Opportunity (or derive from stage).
- [ ] Standard cadence: 3-5 touches over ~3 weeks (day 3/7/14), then park as no_response.
- [ ] Surface 'awaiting response / due for follow-up' as an actionable view."

mk "$CRM" \
  "Company-side follow-up: track recruiter relationship after apply" \
"**From** $AUDIT — recruiter lens.

Once an application is sent, nobody tracks the recruiter relationship that
makes it move. The application is in Twenty, but the human contact driving it
is not connected to it.

**DoD**
- [ ] Link Opportunity → Person (recruiter/hiring manager) for applied roles.
- [ ] Track recruiter touchpoints on the application (outreach, replies, calls).
- [ ] Rejected/ghosted roles retain the recruiter relationship for future roles."

mk "$CRM" \
  "Design isolated data paths for student/coach feedback BEFORE any data exists" \
"**From** $AUDIT — security/privacy lens.

Onboarding students as test subjects means collecting *their* CVs, applications,
and outcomes. Responsibility for protecting that data rests with the repo owner.
Design sanitized/isolated data paths now — easier to design in than retrofit.

**DoD**
- [ ] Decide: students get isolated/sanitized paths, not the owner's live data.
- [ ] Document what is collected, where it lives, who can see it.
- [ ] Confirm the `crm` repo stays private and student data is never committed
      to a public repo."

echo "== done =="
