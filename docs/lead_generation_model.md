# Lead Generation Model

Two perspectives: sales/CRM (how a BD person thinks about pipeline) and data
engineering (what pipelines could produce qualified leads). Synthesized into
a unified lead generation model.

---

## 1. Sales & CRM Perspective

### The Freelance Consultant's Sales Funnel

```
LEAD SOURCES          QUALIFICATION        OUTREACH         PROPOSAL        CLOSE       DELIVERY
                                                               │
┌──────────┐         ┌──────────┐       ┌──────────┐     ┌──────────┐   ┌──────────┐
│ Job      │         │ Budget   │       │ LinkedIn │     │ Rate card│   │ Contract │
│ Boards   │────────▶│ signal   │──────▶│ message  │────▶│ proposal │──▶│ signed   │
│ Free-work│         │ Tech fit │       │ or email │     │ or call  │   │          │
│ HiringCaf│         │ Language │       │          │     │          │   └──────────┘
└──────────┘         │ Location │       └──────────┘     └──────────┘
                     │ Urgency  │
┌──────────┐         └──────────┘
│ Freelance│
│ Platforms│
│ Malt     │
│ Comet    │
│ Crème    │
└──────────┘

┌──────────┐
│ Recruiter│
│ Network  │
└──────────┘

┌──────────┐
│ Inbound  │
│ Content  │
│ Portfolio│
└──────────┘

┌──────────┐
│ Crunchbase
│ Funding  │
│ Signals  │
└──────────┘
```

### What We Cover Today

| Funnel Stage | Covered? | How |
|---|---|---|
| Lead Sources | Partial | Job board scraping only (free-work, hiringcafe) |
| Qualification | Partial | Pipeline scoring (pay, flexibility, tech match) — but no outreach-specific qualification |
| Outreach | Partial | Cold-outreach skill drafts messages; human sends |
| Proposal | No | No rate card templates, no proposal automation |
| Close | No | Manual; tracker captures outcome post-hoc |
| Delivery | N/A | Outside scope of job search tooling |

### What a Sales Professional Would Add

1. **Lead scoring that drives prioritization** — not just "is this a good job?" but "should I reach out today vs next week vs never?" Signals: JD posting recency ( first 48 hours = 2x odds), funding round recency, tech stack match depth, language compatibility, whether the poster is an ESN (higher response rate) or direct employer.

2. **Multi-source lead unification** — the same company appearing on free-work, hiringcafe, and Malt should be one lead with multiple touchpoints, not three separate rows. A CRM would deduplicate on company name + role similarity.

3. **Pipeline velocity metrics** — how many leads enter per week? What's the conversion rate at each stage? What's the average time from lead → outreach → reply → close? Without these, you can't forecast income or identify bottlenecks.

4. **Follow-up cadence** — CRM would track: outreach sent → no reply after 5 days → follow-up → no reply after 10 days → close as no_response. Without automation, this falls through the cracks.

5. **Warm vs cold lead distinction** — a recruiter who reached out to YOU is a warm lead (inbound). A job posting you found is cold (outbound). Warm leads convert at 5-10x the rate and should jump the queue.

### Qualification Signals (Hot vs Cold)

| Signal | Weight | Hot Lead Indicator |
|---|---|---|
| JD posted < 48 hours ago | High | Fresh posting = less competition |
| Explicit freelance/contract mention | Critical | Direct match to preference |
| English-language JD in France | High | No French requirement = accessible |
| ESN posting (not direct employer) | Medium | ESNs hire faster, higher volume |
| Tech stack overlap ≥ 70% | High | Strong fit → higher response rate |
| Salary/day rate disclosed and competitive | High | Budget confirmed, not fishing |
| Company raised funding < 6 months ago | Medium | Hiring budget likely expanding |
| Recruiter reached out to us (inbound) | Critical | Warm lead — skip to proposal stage |
| JD mentions "urgent" or "immediate start" | Medium | Timeline pressure = faster close |

| Signal | Weight | Cold Lead Indicator |
|---|---|---|
| JD posted > 30 days ago | High | Likely filled or stale |
| French-only JD | Critical | Language dealbreaker |
| CDI only, no freelance option | Medium | Preference mismatch |
| Vague JD ("various missions") | Medium | Fishing expedition, no real role |
| No salary/rate disclosed | Low | Common in France, not disqualifying |

---

## 2. Data Engineering Perspective

### Current Pipeline Architecture

```
scrape_freework ──┐
                  ├── merge ── enrich ── score ── jobs_ranked.csv
scrape_hiringcafe─┘              │
                                 ├── translate (deepseek)
                                 ├── extract tech/competencies
                                 ├── classify (seniority, role, sector)
                                 ├── company stats (yfinance)
                                 └── company research (LLM)
```

### Lead Sources Beyond Job Boards

| Source | Data Available | API/Access | Pipeline Fit |
|---|---|---|---|
| **Crunchbase** | Funding rounds, company size, industry, HQ location | API (paid), manual export | Funding round → hiring signal: companies that raised Series A/B in last 6 months are likely hiring data teams. Cross-reference with known companies for outreach. |
| **Malt / Comet / Crème de la Crème** | Freelance missions, day rates, tech requirements | No public API; manual browsing or RSS-style scraping | French freelance platforms are the primary market for contract data engineers. Even periodic manual review would surface leads our job board scrapers miss. |
| **LinkedIn** | Company growth (employee count delta), job postings, employee profiles | API restricted; web scraping fragile | Headcount growth over 6 months = hiring signal. "We're hiring" posts from data leaders = warm leads. |
| **Google Trends** | Search volume for "data engineer freelance Paris", "Microsoft Fabric consultant" | Free API | Demand trend signals — rising search volume for specific tech stacks indicates market demand before it appears in job postings |
| **Stack Overflow / GitHub** | Technology adoption curves, company open-source activity | Public APIs | Companies actively maintaining data tools on GitHub are investing in data engineering. Technology trajectory signals which skills to emphasize. |
| **Tech Conference Speaker Lists** | Companies investing in data (sending speakers) | Manual scraping of event pages | Companies that send engineers to speak at Data + AI Summit, PyData, etc. are investing in data teams. Rich source of target companies and contacts. |
| **French Tech Ecosystem** | French Tech 120, Next40, French Tech Seed — government-labeled startups | Public lists, free | Curated lists of high-growth French startups. Directly relevant to Paris market. Updated annually. |
| **Company Blog / Engineering Blog** | Technical content, hiring announcements, team profiles | RSS feeds, web scraping | Companies that publish data engineering blog posts are investing in data. Author names are potential contacts. |
| **EU Funding Databases** | Horizon Europe grants, BPI France funding, regional innovation grants | Public databases | Companies receiving EU/French government R&D grants in data/AI are hiring. Less competitive signal than Crunchbase. |

### Proposed Pipeline Assets (Dagster)

```
                          ┌── crunchbase_ingest ── funding_signal ──┐
                          │                                         │
scrape_freework ──┐       │                                         │
                  ├── merge ── enrich ── score ── lead_score ── leads_ranked
scrape_hiringcafe─┘       │                    │
                          │                    ├── company_monitor (tracks changes over time)
                          │                    │
                          └── malt_scrape ────┘
                               (manual/semi-automated)
```

**New assets:**

| Asset | Input | Output | Schedule |
|---|---|---|---|
| `crunchbase_ingest` | Manual CSV export from Crunchbase | `data/crunchbase_companies.json` | Monthly (manual trigger) |
| `funding_signal` | Crunchbase data | Flag on Company: `funding_round_recent`, `funding_total` | After crunchbase_ingest |
| `malt_scrape` | Manual browsing / saved searches | `data/malt_missions.json` | Weekly (semi-automated) |
| `company_monitor` | merge + enrich output from prior runs | Company-level deltas: new job postings count, headcount estimate change, new funding | After each pipeline run |
| `lead_score` | score output + funding_signal + company_monitor | `leads_ranked.csv` with lead-specific scoring | After scoring |

**Lead scoring model** (separate from job scoring):
- **Intent score** (0-1): Is this company actively hiring? JDs posted recently, funding raised, office expansion
- **Fit score** (0-1): Does the role match our preferences? Tech stack, language, location, contract type
- **Access score** (0-1): Can we reach a decision-maker? Known contacts, recruiter network, warm intro path
- **Urgency score** (0-1): How time-sensitive? JD age, "urgent" keywords, funding timing
- **Composite: Intent × Fit × (Access + Urgency) / 2**

### Data Quality Considerations

| Issue | Impact | Mitigation |
|---|---|---|
| Stale job postings | Wasted outreach to filled roles | Track `date_posted`; auto-deprioritize after 30 days; re-scrape to detect removal |
| ESN vs end client confusion | Wrong outreach target, wrong company research | Already modelled in `end_client_name/sector`; improve extraction reliability (v4-flash JSON fix pending) |
| Duplicate detection | Same job on multiple boards → double-counting | Normalize company name + title similarity; use URL as primary key where available |
| Company name variants | "Modis" vs "Modis (now Akkodis)" vs "Akkodis" | Fuzzy matching with manual review for high-value leads |
| French language JDs slipping through | Auto-rejected at dealbreaker gate | Language detection in pipeline enrichment (already implemented: `fr_count > 0 → needs translation`) |
| Rate data missing | Can't assess compensation fit | French freelance platforms typically show day rates; job boards rarely do — need platform scraping for rate benchmarks |

---

## 3. Lead Sources: Unified Model

```
                           LEAD SOURCES
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
   JOB BOARDS             FREELANCE PLATFORMS       PROACTIVE
        │                       │                       │
  ┌─────┴─────┐           ┌─────┴─────┐           ┌─────┴─────────┐
  │ free-work │           │ Malt      │           │ Crunchbase     │
  │ hiringcafe│           │ Comet     │           │ funding rounds │
  └───────────┘           │ Crème     │           │ French Tech 120│
                          └───────────┘           │ Conference     │
                                                   │ speaker lists  │
        │                       │                 │ LinkedIn       │
        │                       │                 │ growth signals │
        ▼                       ▼                 └───────────────┘
  ┌──────────────────────────────────────────────────────────────┐
  │                    LEAD QUALIFICATION                        │
  │                                                              │
  │  Dealbreaker check ──→ Intent score ──→ Fit score ──→ Access │
  │  (language, type)      (are they hiring?)  (do we match?)    │
  └──────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                    LEAD ROUTING                              │
  │                                                              │
  │  Hot leads (score > 0.7) ──→ Outreach this week             │
  │  Warm leads (0.4-0.7)   ──→ Monitor, re-score weekly         │
  │  Cold leads (< 0.4)     ──→ Archive, revisit if signals change│
  └──────────────────────────────────────────────────────────────┘
                                │
                                ▼
                          OUTREACH
                     (cold-outreach skill)
                                │
                                ▼
                          APPLICATION
                     (new-application skill)
```

### Lead Source Comparison

| Source | Volume | Quality | Automation | Current Status |
|---|---|---|---|---|
| Job board scraping | High (100+/week) | Medium (curated postings) | Fully automated | **Implemented** |
| Freelance platforms | Medium (20-50/week) | High (day rates visible, freelance-native) | Manual / semi | **Not implemented** |
| Crunchbase signals | Low (5-15/month) | Very high (funding = hiring intent) | Manual CSV import | **Not implemented** |
| Recruiter inbound | Low (1-5/month) | Very high (warm lead) | Manual | **Not implemented** |
| Content/portfolio inbound | Low (1-3/month) | Very high (pre-qualified) | N/A (human creates content) | **Not implemented** |
| LinkedIn growth signals | Medium (requires enrichment) | Medium (growth ≠ hiring) | Requires API/scraping | **Not implemented** |
| Conference/ecosystem | Low (periodic) | High (curated, high-growth) | Manual research | **Not implemented** |

---

## 4. Gaps & Next Steps

### Critical (blocks lead flow)

1. **No freelance platform coverage** — the French freelance market lives on Malt, Comet, and Crème de la Crème. Our job board scrapers miss this entirely. Even a weekly manual review of saved searches would 2-3x the lead pipeline.

2. **No lead scoring for prioritization** — all qualified jobs get equal treatment. A funding-round startup hiring urgently should jump the queue over a 30-day-old CDI posting.

### Important (improves conversion)

3. **No funding-round signal** — Crunchbase data is the highest-signal lead indicator (company raised money → hiring) and we have no ingestion path for it.

4. **No company monitoring** — we research a company once and move on. Companies that posted 3 data roles in 2 months are aggressively hiring; companies with 0 postings in 6 months may have frozen. This delta is invisible.

5. **No warm/cold lead routing** — a recruiter who messages you on LinkedIn should bypass the entire qualification pipeline and go straight to proposal. Currently indistinguishable from a job board scrape.

### Nice-to-have (future)

6. **Pipeline velocity dashboard** — leads/week, outreach response rate, time-to-first-reply, conversion by source. Needed to optimize spend (which lead sources produce the best ROI?).

7. **Rate benchmarking** — what's the market day rate for a Fabric data engineer in Paris with 6 years experience? Without this, every negotiation starts blind. Malt/Comet scraping would provide this passively.
