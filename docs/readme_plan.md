# Sharing README plan — opt-in CRM/BD depth

> **Status**: plan / spec. Not the final README. This documents what the sharing
> README should communicate and the opt-in framework it must support, so the
> README can be written once the tooling settles.

## Goal

When this toolkit is shared (public repo + student-coach cohort), the README
must (1) explain the cross-domain elements and diagram the architecture, and
(2) let each user choose how deep they want to go into CRM / business-development
territory. Not everyone wants (or needs) the full funnel spectrum.

## Cross-domain map (what the README must convey)

The toolkit spans disciplines most job-seekers don't think of as connected.
The README should name them so a user sees the full shape up front:

| Domain | What the toolkit does | Depth tier |
|---|---|---|
| **Data engineering** | Scrape → enrich → score → rank job boards; DuckDB medallion | core |
| **Recruiting** | Application workflow: tailor CV per JD, track funnel to interview/offer | core |
| **Sales / BD** | Cold outreach (role + relationship + service playbooks), lead scoring, follow-up cadence | opt-in |
| **CRM** | Twenty as system of record: Opportunity, Person, Outreach, referral tracking | opt-in |
| **Product / ops** | Weekly ritual, feedback loop, analytics, inbound attribution | opt-in |

## Opt-in framework — three tiers

Users pick a tier at setup. Tiers are additive; a user can start low and opt up.

### Tier 1 — JD ingestion + applying (core)
- Scrape boards → score → shortlist → tailor CV → apply → track in a CSV.
- No CRM, no outreach, no lead scoring. Single-user, low setup.
- Deliverable: ranked job list + per-application tailored CV + funnel CSV.

### Tier 2 — Application funnel + tracker (recruiting focus)
- Adds the Twenty CRM as system of record: Opportunity pipeline, ATS score,
  response-rate stats.
- Still application-centric; outreach optional.
- Good for: someone who mainly wants to apply better and track it.

### Tier 3 — Full CRM / BD spectrum (portfolio- or sales-oriented)
- Adds cold outreach playbooks (role / relationship / service / event),
  lead scoring + routing, follow-up cadence, referral tracking, inbound
  attribution, weekly ritual.
- Good for: freelancers, consultants, portfolio-driven job-seekers who want
  proactive leads, not just applications.

## How a user opts in

- A `tier:` field in `job_search_preferences.yaml` (or a `SETUP.md` question).
- The README shows the three tiers as a table + a small decision tree:
  "Want to apply to posted jobs → Tier 1. Want to apply well and track →
  Tier 2. Want proactive pipeline + relationships → Tier 3."
- Each tier's section lists what you get, what it needs (Docker for Twenty at
  T2+, API keys), and links to the relevant skills.
- Skills are already gated by human approval, so opting up simply means
  "enable these skills"; opting down means ignoring them.

## Diagram(s) the README should include

1. **Architecture flow** (existing README has the DAG) — keep, but label each
   stage with its domain and tier.
2. **Funnel map** — the two-funnel picture: lead-gen funnel (awareness →
   relationship → qualified lead) feeding the sales/application funnel
   (lead → apply → interview → offer), plus the feedback loop feeding outcomes
   back into scoring.
3. **Tier ladder** — visual of the three tiers stacking.

## Key message to land

> Most job-seekers treat job-hunting as "apply to postings." That's Tier 1.
> The full toolkit also gives you the business-development layer (outreach,
> relationships, referrals) that converts far better — but you opt into it
> when you're ready.

## Open questions (for the author)

- Should tiers map to CLI subcommands / a setup wizard, or just documentation
  + skill gating? (Recommend: documentation + skill gating first — simplest.)
- Where does the student-coach onboarding sit? (Recommend: a separate
  `STUDENTS.md` or setup path that defaults to Tier 2 with coach guidance.)
- Should the README be split (main README + per-tier docs) to avoid a wall of
  text? (Recommend: main README with tier table + decision tree; per-tier
  detail in docs/.)
