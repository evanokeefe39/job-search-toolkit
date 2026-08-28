# Cross-Domain Blindspot Audit

> **Source**: expert-panel review of `job-search-toolkit` + `crm` (2026-08-27).
> Triggered by a realization that job-seeking is a multi-domain exercise
> (sales, recruiting, CRM, product, operations, data engineering) and the
> primary author is a data engineer, not an expert in the others.

The core finding, agreed by every expert from a different angle:

> **The system produces applications but does not consume outcomes.**

Everything is front-loaded — scrape → enrich → score → tailor → ready.
There is no return leg that feeds *what actually happened* (applied →
interview → offer → rejected → ghosted) back into scoring, tailoring, or
outreach. `tracker.csv` is the proof: all rows sit at `ready`/`tailoring`,
zero `applied`. The funnel has no feedback loop.

## Other funnels we were blind to

Beyond the two originally considered (sales/application + lead-gen/relationship):

1. **Feedback/learning funnel** — outcomes feed back into scoring/tailoring.
   Missing entirely today; the single highest-value gap.
2. **Referral funnel** — a qualified warm intro skips the application funnel.
   Distinct from cold outreach and relationship-building; not tracked.
3. **Inbound/content funnel** — portfolio, market-research reports, content as
   lead magnets. `outreach_tracker` already has a `direction: inbound` field,
   never used. Inbound converts 5-10x cold.
4. **Event/community funnel** — attend → follow up → relationship. CRM already
   models `Event`; no cadence or conversion math.
5. **Time/effort funnel** — where does effort go by stage? Prevents the
   "ready pile" trap (over-tailor, under-apply is a resource problem, not a
   sales problem).
6. **Product funnel** — sharing the repo makes it a product and
   students/coaches the users: onboard → use → feedback → refer. Currently
   undefined. (Meta to this repo: the repo owner does this, not the users.)

## Panel findings by discipline

### Sales / BD
- Pipeline stops at `ready` because there is no close discipline.
- Cold outreach is ideas-stage; follow-up cadence undefined.
- `outreach_tracker` is per-message, not per-relationship (loses the thread).
- Lead-scoring model (Intent × Fit × (Access+Urgency)/2) is strong on paper
  but nothing routes leads into outreach by score — scoring and outreach are
  disconnected.
- Add "next action + by when" to every Opportunity.

### Recruiter
- Applying cold to public JDs is the weakest channel.
- The `ready` pile is a trap: 12 ready, 0 sent = the tool became the end.
- Volume is a delivery problem, not a quality problem (re-read TechTalk's
  "3/day" advice in this light).
- No company-side follow-up: once `ready` is sent, nobody tracks the recruiter
  relationship that makes it move.

### Data engineering (the home lens, hardest to see)
- The five scoring dimensions are *preference-weighted* (pay 0.30, flexibility
  0.25...), not *outcome-weighted*.
- No ground-truth labels: outcomes are never fed back to retune weights.
- Scoring is an unvalidated hypothesis because the feedback leg is missing.
- Cleanest, most tractable fix; pure data-engineering work.

### Product / PM
- Student-coach Discord is the highest-leverage item, currently a blank.
- Students provide outcome labels + volume the repo structurally lacks.
- Design onboarding (coach-led kickoff, shared preferences, feedback cadence)
  and instrument it — student response rates are the validation set.
- Without structure: anecdotes, not data.

### Operations / SOP
- No cadence. Every skill is reactive ("when asked").
- Nothing schedules weekly refresh / monthly market review / outreach follow-up.
- Phase 5 analytics can't run without a rhythm trigger.
- A single weekly ritual (refresh → triage → follow-up → review) would
  operationalize the whole system.

### Security / privacy
- Handled well so far: `resume/` + `applications/` gitignored, `crm` private,
  `.env` guarded.
- New risk: student/coach feedback may collect *their* CVs, applications,
  outcomes. Decide isolated/sanitized data paths *before* any data exists.
- Easier to design in than retrofit.

## The through-line

Every discipline converged on the same structural gap: **no outcome feedback.**
The `ready` pile and the missing feedback leg are the same problem. The single
highest-leverage change is closing the loop: send applications, record
outcomes, feed them back into scoring. The student-coach program is the fastest
path to outcome data at volume. Everything else (outreach, scoring, CRM
structure) is downstream of having real outcome data to steer with.

## Companion: cold-outreach playbook split

Cold outreach is not one activity. It should be split into distinct playbooks,
each with its own SOP, message style, cadence, CRM tag, and success metric:

| Playbook | Goal | Contact | Metric | Funnel |
|---|---|---|---|---|
| Role-anchored | interview/placement for a specific JD | recruiter / hiring manager tied to the role | response → meeting → placement | sales funnel |
| Relationship-building | be remembered, top-of-mind | practitioners, peers, community | network breadth, referrals, warm intros | lead-gen funnel |
| Service/solution | sell an offering (audit, workshop) | CTO / Head of Data with a trigger | conversation → proposal | consultative |
| Warm/inbound | convert fast | they contacted us / warm intro | conversion speed | skip to close |
| Event/context | follow up after meetup/talk | shared-context contact | conversation | lead-gen |

CRM distinction: **Contact** (Person, durable) vs **Touch** (each Outreach
message, multi-row per person with `playbook` + `touch_number`). Follow-up
cadence becomes a query ("any Person untouched in 7+ days?"), not guesswork.

## Ticket map

Each gap/point above is filed as a GitHub issue (see issue list in this repo
and `crm`). Nothing should fall through.
