# ROADMAP.md — job_search_scraping

Phased roadmap capturing all lead generation, application, revenue, and
learning pipelines. Updated as new scope emerges.

---

## Phase 0: Foundation (in progress)

What's already built and working.

### Discovery

- [x] **Job board scraping** — 10 boards: free-work.com, hiringcafe.com, hellowork.com, englishjobs.fr,
  faruse.com, weworkremotely.com, remoteok.com, datasciencejobs.com, plus linkedin.com
  (two sources: `linkedin_jobs` listings + `linkedin_posts` recruiter posts)
- [x] **Pipeline enrichment** — Dagster Kimball star schema: scrape → upsert →
  score → exports → gold views. Ranking is decoupled from LLM (pure tabular);
  optional `--enrich` runs deferred, dimension-scoped company research
  (1 LLM call per company, not per row)
- [x] **Ranked export** — `jobs_ranked.csv` with five-dimension scoring
  (pay, flexibility, responsibility, tech match, company quality);
  company_quality uses tabular heuristics + dim_company join, zero LLM
- [x] **LinkedIn boards** — recruiter posts + job listings land in `silver.jobs` as
  two boards (`linkedin_posts`, `linkedin_jobs`): Apify/Tavily discovery → JSON-LD
  parse → dedup → deterministic tech scan + regex post→job extraction → bronze;
  deferred `linkedin_post_enriched` LLM pass fills the regex gap (2026-08-23)

### Application Workflow

- [x] **jd-refresh skill** — run discovery, report delta, stop for shortlist
- [x] **new-application skill** — scaffold application folder, company research (web_search, yfinance, Crunchbase checkpoint), dealbreaker gate, tracker entry
- [x] **tailor-resume skill** — Resume-Matcher (Docker, deepseek-chat), diff log review, tailored RenderCV YAML, audit, PDF
- [x] **application-tracker skill** — Twenty funnel transitions, response-rate stats

### Research & Docs

- [x] **ATS/resume knowledge base** — ATS system behavior (2026), prompt injection data, job search strategy
- [x] **Data model** — 6 entities: Company, JobDescription, Person, Agency, Application, Outreach
- [x] **Lead generation model** — sales/CRM + data engineering audit, unified lead source framework
- [x] **Remote job boards catalog** — 30+ Europe-focused remote job boards
- [x] **ATS matcher catalog** — 16 tools evaluated for federated pipeline (superseded by skill approach)

---

## Phase 1: Lead Generation Expansion

Goal: go from 1 lead source (job boards) to 5+, covering freelance-native
platforms and proactive signals.

### Pipeline Assets

- [ ] **Crunchbase funding signal** — `crunchbase_ingest` → `funding_signal` asset. Manual CSV import from Crunchbase export; flag companies that raised within 6 months. Cross-reference with known companies from merged_jobs. Output: `data/crunchbase_companies.json`
- [ ] **Company monitoring** — track company-level deltas across pipeline runs: new job postings count, estimated headcount change, new funding events. Output: `data/company_monitor.json`
- [ ] **Lead scoring model** — separate from job scoring. Intent × Fit × (Access + Urgency) / 2. Combines job board scrapes, funding signals, and company monitoring into a unified `leads_ranked.csv`
- [ ] **Freelance platform research** — manual/semi-automated Malt, Comet, Crème de la Crème review. Saved searches, rate benchmarks. Output: `data/malt_missions.json` (manual curation to start)
- [ ] **Source ingestor plugin framework** — Python entry-point registry (`job_search_toolkit.sources`) so community users can add custom job-board ingestors. Extension contract: fetch raw → normalize to `CanonicalJob` → bronze layer; existing scrapers become reference implementations. Decision (2026-08-11): Python plugin architecture, NOT dlt as core — Dagster already orchestrates, DuckDB is the sink, and scrapers are framework-free (429 handling, buildId handshake, French parsing). Revisit dlt only if multi-destination loading (Postgres/BigQuery) becomes a goal

### Skills

- [x] **market-research skill** — multi-level territory analysis (macro → tech → data eng), headwinds/tailwinds, territory viability
- [ ] **lead-qualification skill** — score and prioritize leads across all sources; hot/warm/cold routing; dealbreaker re-check against preferences
- [ ] **lead-dashboard skill** — pipeline velocity metrics (leads/week, conversion by source, time-to-first-reply); can start as manual CSV analysis

### New Lead Sources

- [ ] **Freelance platforms** — Malt, Comet, Crème de la Crème. French-native, rate-transparent, freelance-first. Even weekly manual review would 2-3x pipeline
- [ ] **Crunchbase funding** — companies raising Series A/B are hiring data teams. Highest-signal lead indicator
- [ ] **LinkedIn growth signals** — headcount deltas, "we're hiring" posts, CTO/Head of Data activity
- [ ] **French Tech 120 / Next40** — government-curated high-growth startups in France. Public list, annual refresh
- [ ] **EU funding databases** — Horizon Europe, BPI France grants in data/AI. Lower signal than Crunchbase but public and free
- [ ] **Creator/social profiles** — LinkedIn posts, GitHub activity, conference speaker lists → contact discovery and market intelligence

---

## Phase 2: Outreach & Relationship Building

Goal: go from "apply to job postings" to "build a professional network that
produces warm inbound leads."

### Skills

- [x] **cold-outreach skill** — find contacts (data team, hiring managers, recruiters), draft messages, human review gate, outreach tracker
- [ ] **recruiter-relationship skill** — track recruiter contacts over time; follow-up cadence; maintain "warm network" list separate from cold outreach
- [ ] **event-scout skill** — discover, qualify, and track events (conferences, meetups, webinars) relevant to Paris data engineering. Validate: is it worth attending? Output: `data/events_tracker.csv`

### Pipeline Assets

- [ ] **Contact discovery** — given a company, find data team members on LinkedIn, GitHub, company blog. Automated name extraction from research.md + web_search → `data/contacts.csv`
- [ ] **Recruiter agency database** — seed and maintain list of agencies operating in target regions. Started in `job_search_preferences.yaml`; grow from market-research and cold-outreach discoveries

### Event Pipeline

- [ ] **Event discovery sources:**
  - Meetup.com — Paris data engineering, Python, Azure/Fabric groups
  - Conference websites — PyData Paris, Data + AI Summit, Microsoft Fabric Community Conference, AWS re:Invent, KubeCon EU
  - LinkedIn Events — tech talks, webinars, panel discussions
  - Eventbrite — local tech events, workshops, hackathons
  - University/public research events — INRIA, École Polytechnique, Télécom Paris

- [ ] **Event qualification criteria:**
  - Location: Paris/Île-de-France or virtual (CET-compatible timezone)
  - Language: English or English-friendly (French events with English content acceptable)
  - Relevance: data engineering, cloud architecture, analytics, Fabric/Azure
  - Cost: free preferred; paid if strong networking opportunity
  - Networking potential: speaker list includes target-company engineers or recruiters
  - Recency: events within next 90 days prioritized

---

## Phase 3: Revenue Streams

Goal: diversify income beyond freelance contracts into scalable, productized
offerings.

### Freelance Contracts (Primary)

- [ ] **Rate benchmarking pipeline** — scrape Malt/Comet for day rates by tech stack and seniority in Paris. Output: `data/rate_benchmarks.json`. Uses: proposal pricing, negotiation leverage
- [ ] **Proposal/rate card templates** — standardize the "here's my rate, here's what I deliver" response. Not automated — human fills in specifics per opportunity
- [ ] **Contract templates** — French freelance contract templates (portage salarial, auto-entrepreneur, EURL). Legal boilerplate, not advice

### Productized Services

- [ ] **One-off consultations** — "Fabric architecture review" or "data platform health check." Fixed-price, fixed-scope engagement. Lead source: inbound from portfolio content + cold outreach to companies posting Fabric job ads (they have Fabric, they may need help before hiring)
- [ ] **Training / workshops** — "Microsoft Fabric for Data Engineers" (1-day workshop). Target: ESNs training their consultants, companies adopting Fabric. Lead source: companies posting Fabric roles (they're building Fabric teams → training demand)
- [ ] **Guides / courses** — written or video content monetized directly. Examples: "From SSIS to Fabric: a migration playbook," "DAX for data engineers" (leverage existing Power BI knowledge). Distribution: Gumroad, self-hosted, or platform-specific

### Lead Generation for Services

| Service | Lead Signal | Outreach Approach |
|---|---|---|
| Fabric architecture review | Company posting Fabric roles (they have Fabric, may need expertise before team is hired) | Cold outreach: "I noticed you're building a Fabric team — I do architecture reviews for companies in that phase. Happy to chat." |
| Data platform health check | Company with multiple data roles posted (scaling pains) | Cold outreach to CTO/Head of Data: "Scaling data platforms is what I do — want a second opinion on your architecture?" |
| Fabric workshop | ESN posting Fabric roles (they need to train consultants) | Cold outreach to ESN training/ practices lead: "I run a 1-day Fabric workshop for data engineering teams — interested?" |
| DAX for data engineers guide | Companies posting Power BI + data engineering roles (crossover demand) | Content marketing: publish free chapter, gate the rest. Promote via LinkedIn posts, dev.to, and data engineering communities — no paid ads.

---

## Phase 4: Learning & Market Education

Goal: stay current, build expertise, surface opportunities through continuous
learning.

### Events (qualified — see Phase 2 event pipeline)

- [ ] **Conference attendance** — PyData Paris, Data + AI Summit, Microsoft Fabric Community Conference, AWS re:Invent, KubeCon EU, Devoxx France, dotAI, Big Data Paris
- [ ] **Local meetups** — Paris Data Engineers, Paris Python, Azure Paris User Group, dbt Meetup Paris, Data + AI France
- [ ] **Webinars** — Microsoft Fabric monthly updates, Databricks tech talks, dbt Labs webinars, AWS/Azure/GCP what's-new sessions
- [ ] **Hackathons** — Fabric hackathons, startup weekend data editions, open data challenges

### Creator & Expert Relationships

- [ ] **Identify key voices** in French/European data engineering: who writes about Fabric, Azure data, dbt, data platform architecture? Follow, engage, learn
- [ ] **Potential collaborators** — creators whose audience overlaps with target customers. Joint webinars, guest posts, cross-promotion
- [ ] **Mentor/advisors** — senior data engineers or freelance consultants in Paris who've done what we're trying to do. Coffee chats, not cold outreach
- [ ] **LinkedIn peer discovery (deferred 2026-08-17)** — extend the LinkedIn
      source adapter with a practitioner/peer pass: devs and data engineers in
      Paris/Europe with shared interests (Fabric/Azure/dbt). Surfaces as Person
      contacts (`contact_type=data_team`/`network`) into the silver people
      contract. Deferred until the recruiter + job pipeline validates — not
      dropped.

### Continuous Market Education

- [ ] **Market research cadence** — monthly territory viability reports via market-research skill. Feed into lead prioritization
- [ ] **Technology watch** — new Azure/Fabric features, competitive tools (Databricks, Snowflake), regulatory changes (EU data sovereignty, French contractor law). Source: official blogs, Hacker News, r/dataengineering
- [ ] **Rate trend tracking** — quarterly rate benchmark updates from freelance platforms. Are Paris day rates rising or falling? Which tech stacks command premiums?

---

## Phase 5: Analytics & Optimization

Goal: measure what works, kill what doesn't, double down on winners.

### Pipeline Analytics

- [ ] **Lead source attribution** — which sources produce the best leads? Track: source → qualified → outreach → response → interview → offer. Output: conversion funnel by source
- [ ] **Response rate by message type** — A/B test outreach message templates. Which phrasing gets replies? Track in outreach_tracker.csv
- [ ] **Time-to-hire benchmarking** — how long from application to offer? Track by company type (ESN vs direct), role, region
- [ ] **Revenue forecasting** — active leads × historical close rate × average day rate = projected monthly income. Crude but directionally useful

### Content Analytics

- [ ] **Portfolio/content attribution** — which blog posts, projects, or reports drive inbound inquiries? Track source in outreach_tracker.csv (`direction: inbound`)
- [ ] **LinkedIn post engagement** — what content resonates with target audience? Track impressions, comments, profile views, inbound messages after posting

---

## Dependency Graph

```
Phase 0 (Foundation)
  ├── Phase 1 (Lead Gen Expansion)
  │     ├── Phase 2 (Outreach & Relationships)
  │     │     ├── Phase 3 (Revenue Streams)
  │     │     └── Phase 4 (Learning & Education)
  │     └── Phase 5 (Analytics)
  └── (Phase 5 depends on data from all prior phases)
```

Phase 1 enables everything downstream — without more lead sources, there's
nothing to optimize. Phase 2 (outreach) and Phase 3 (revenue) can proceed
in parallel once Phase 1 delivers. Phase 4 (learning) is ongoing and
independent. Phase 5 (analytics) needs data from all prior phases.

---

## Prioritization Heuristic

For any unstarted item, score it: **Impact (1-5) × Confidence (1-5) / Effort (1-5).**
Highest score first. Re-score quarterly.

**Current top candidates (preliminary):**

| Item | Impact | Confidence | Effort | Score | Phase |
|---|---|---|---|---|---|
| Freelance platform manual review | 5 | 4 | 1 | 20.0 | 1 |
| Crunchbase funding signal | 4 | 4 | 2 | 8.0 | 1 |
| Lead scoring model | 5 | 3 | 3 | 5.0 | 1 |
| Event discovery (manual) | 3 | 5 | 1 | 15.0 | 2 |
| Rate benchmarking | 4 | 4 | 2 | 8.0 | 3 |
| Contact discovery automation | 3 | 3 | 3 | 3.0 | 2 |
| Market research reports as lead magnet | 4 | 3 | 2 | 6.0 | 3 |
| Fabric architecture review service | 3 | 3 | 2 | 4.5 | 3 |
| Pipeline velocity dashboard | 2 | 4 | 2 | 4.0 | 5 |

---

## Potential Enhancements (deferred)

- **CLI robustness micro-fixes** (2026-08-25 design review, see
  `tasks/plans/cli-source-selection.md`): make partial failures survivable in
  one run (`raise_on_error=False` + surface failed step keys + recovery hint),
  add `RetryPolicy` to scrape assets, and fix the freework `_max_pages()` leak.
  Cheap wins; no new CLI surface.
- **LinkedIn jobs via guest API (done 2026-08-25):** `linkedin_jobs` now
  discovers via LinkedIn's public guest jobs API (free, no auth) — resolves the
  prior under-yield. **FRAGILE** (undocumented endpoint); if it breaks, see
  `docs/linkedin-source-spike.md` for fallbacks (dedicated actors / google-
  scraper). Original diagnosis: SEO landing pages were discarded; now moot.
- **LinkedIn posts → jobs enrichment** (2026-08-25): recruiter posts become
  jobs via a regex verdict (`land`/`queue`/`drop`); explore recruiter-region
  inference (APAC/EMEA/DACH/USA) via regex vs LLM, and close the `queue` LLM
  pass gap. See `tasks/plans/linkedin-posts-to-jobs.md`.
- **Per-board Dagster partitions** (future): if the pipeline becomes scheduled
  or multi-user, model each board as a static partition for native selective
  runs + backfill (see `docs/pipeline-streaming-research.md`). Requires a
  persistent DagsterInstance. Deliberately NOT building now.

---

## What's Out of Scope (for now)

- **Automated application submission** — human review gate stays. No auto-apply.
- **LinkedIn automation/scraping** — fragile, against ToS. Manual review only.
- **Paid advertising** — LinkedIn ads, Google Ads for services. Premature until productized offerings exist.
- **Agency/ESN partnership formalization** — worth exploring but requires legal/tax advice beyond this repo's scope.
- **Multi-language resume versions** — English only until French reaches business proficiency.
