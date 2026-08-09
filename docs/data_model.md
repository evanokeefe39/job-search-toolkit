# Data Model — job_search_scraping

Core entities and their relationships. Market research (headwinds/tailwinds,
territory viability) is captured in prose in `data/market_state.md`, not as
tabular data.

## Entity Relationship Diagram

```
┌──────────┐       ┌────────────────┐       ┌──────────────┐
│  Agency  │       │    Company     │       │    Person    │
│          │──1:N──│                │──1:N──│              │
│ name     │       │ name           │       │ name         │
│ special. │       │ size           │       │ title        │
│ regions  │       │ funding        │       │ linkedin_url │
│ website  │       │ ticker         │       │ email        │
└────┬─────┘       │ reputation     │       │ contact_type │
     │             │ locations      │       │ company_id   │
     │             │ industry       │       │ agency_id    │
     │             └────────┬───────┘       └──────┬───────┘
     │                      │                      │
     │                      │ poster   end_client   │
     │                      │   │         │         │
     │             1:N      ▼   ▼         ▼         │ 1:N
     │    ┌─────────────────────────────────────┐   │
     │    │          JobDescription              │   │
     ├───▶│                                     │◀┐ │
     │    │ poster_company_id                    │ │ │
     │    │ end_client_name (nullable)            │ │ │
     │ N:M│ end_client_sector (nullable)          │ │ │
     │    │ engagement_type (nullable)            │ │ │
     │    │ title                               │ │ │
     │    │ location                            │ │ │
     │    │ pay                                 │ │ │
     │    │ requirements                        │ │ │
     │    │ tech_stack                          │ │ │
     │    │ source_board                        │ │ │
     │    │ url                                 │ │ │
     │    │ date_posted                         │ │ │
     │    └──────────────┬──────────────────────┘   │
     │                   │                          │
     │             1:1   │                          │
     │    ┌──────────────┴──────────┐               │
     │    │      Application        │               │
     │    │                         │               │
     │    │ job_id                  │               │
     │    │ status                  │               │
     │    │ status_dates (JSON)     │               │
     │    │ ats_score               │               │
     │    │ folder_path             │               │
     │    │ outcome                 │               │
     │    │ notes                   │               │
     │    └─────────────────────────┘               │
     │                                              │
     │    ┌─────────────────────────────────────────┘
     │    │ 1:N
     │    ▼
     │   ┌─────────────────┐
     └──▶│    Outreach     │
         │                 │
         │ person_id       │
         │ application_id  │ (nullable)
         │ agency_id       │ (nullable)
         │ channel         │
         │ direction       │
         │ message         │
         │ status          │
         │ sent_date       │
         │ reply_date      │
         │ notes           │
         └─────────────────┘
```

## Entities

### Company

Represents an organization — either a target employer (end client) or a
posting intermediary (ESN/consulting firm). One company can play either
role depending on the job.

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier |
| `name` | string | Legal company name |
| `size` | string | Headcount range (e.g. "51-200", "1000+") |
| `funding` | string | Funding summary (e.g. "Bootstrapped", "Series B $20M") |
| `ticker` | string | Stock ticker if public; null if private |
| `reputation` | string | Free-text reputation notes (sourced) |
| `locations` | list[string] | Office locations |
| `industry` | string | Industry/sector |

**Source:** Pipeline enrichment (`pipeline/enrich_canonical.py`, `stage4_company_stats.py`)
and `new-application` research (Crunchbase, web_search, yfinance).

**Current storage:** Embedded in `merged_jobs.json` as `company_stats` and
`company_deep_research` fields; replicated into `research.md` per application.

---

### JobDescription

Represents a single job posting from any board. In the French market, the
**poster** is often an ESN/consulting firm while the **end client** is the
company where the work is done. This distinction drives everything: the ESN
recruiter is your contact for placement, but the day rate, team, and often
the hiring manager live at the end client.

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier |
| `poster_company_id` | string | FK to Company — the firm that posted the ad (ESN or direct employer) |
| `end_client_name` | string | End client company name; null if posting directly |
| `end_client_sector` | string | End client industry/sector; null if not applicable |
| `engagement_type` | string | `direct_hire`, `esn_placement`, `freelance_mission` |
| `title` | string | Job title |
| `location` | string | Location string (city, country) |
| `workplace_type` | string | `remote`, `hybrid`, `on_site` |
| `pay` | object | `{min, max, currency, frequency, is_disclosed}` |
| `contract_types` | list[string] | `["cdi","freelance","cdd"]` etc. |
| `seniority_level` | string | `junior`, `mid`, `senior`, `lead` |
| `requirements` | string | English description/requirements text |
| `tech_stack` | list[string] | Extracted technologies |
| `source_board` | string | `freework`, `hiringcafe` |
| `url` | string | Original or apply URL |
| `date_posted` | date | Posting date |

**Source:** Scrapers (`scrape_freework.py`, `scrape_hiringcafe.py`) + pipeline
enrichment (LLM extracts `end_client_*` and `engagement_type` from descriptions).

**Current storage:** `merged_jobs.json` (canonical), `jobs_ranked.csv` (scored subset).
The `poster_company_id` maps to an inlined company record in the JSON;
`end_client_name` and `end_client_sector` are sometimes empty (~86% in current
data due to the v4-flash function-call JSON malformation — see AGENTS.md
known sharp edges).

---

### Person

Represents a human contact — employee at a target company, hiring manager,
or recruiter at an agency.

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier |
| `name` | string | Full name |
| `title` | string | Job title |
| `company_id` | string | FK to Company; null for agency recruiters |
| `agency_id` | string | FK to Agency; null for company employees |
| `linkedin_url` | string | LinkedIn profile URL |
| `email` | string | Email address (if known) |
| `contact_type` | string | `data_team`, `hiring_manager`, `recruiter`, `other` |
| `notes` | string | Context — how found, relevance to which roles |

For ESN-posted jobs, People naturally attach to whichever side is relevant:
end-client employees for team research, ESN recruiters for placement contact.

**Source:** `cold-outreach` skill (web_search, LinkedIn discovery).

**Current storage:** Planned: `data/contacts.csv`. Not yet implemented.
`.gitignore` covers `data/contacts*`.

---

### Agency

Represents a recruitment agency known to operate in target regions.

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier |
| `name` | string | Agency name |
| `specializations` | list[string] | e.g. `["data","tech","finance"]` |
| `regions` | list[string] | e.g. `["france","dach","iberia"]` |
| `website` | string | Agency website URL |

**Source:** `cold-outreach` skill (web_search for recruitment agencies in target
regions). Seed list in `job_search_preferences.yaml` → `outreach.recruiter_agencies`.

**Current storage:** Planned alongside Person in `data/contacts.csv`. Not yet
implemented.

---

### Application

Represents one job application — the central workflow entity.

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier |
| `job_id` | string | FK to JobDescription |
| `status` | string | Current workflow status (see below) |
| `status_dates` | JSON | `{"shortlisted": "2026-08-07", "applied": "2026-08-10", ...}` |
| `ats_score` | float | ATS keyword match score (from Resume-Matcher) |
| `folder_path` | string | Path to `applications/YYYY-MM-DD_company_role/` |
| `outcome` | string | Terminal label: `rejected`, `offer_accepted`, `offer_declined`, `withdrawn`, `ghosted` |
| `notes` | string | Free-text notes |

**Source:** `new-application`, `tailor-resume`, `application-tracker` skills.

**Current storage:** `tracker.csv` (flat CSV, 11 columns: `date_added, company,
role, source, url, status, folder, ats_score, applied_date, outcome, notes`).
The CSV maps to this model as:
- `date_added` → first status_dates entry
- `company`/`role` → denormalized from JobDescription for readability
- `source` → JobDescription.source_board
- `url` → JobDescription.url
- `status` → Application.status
- `folder` → Application.folder_path
- `ats_score` → Application.ats_score
- `outcome` → Application.outcome
- `notes` → Application.notes

**Status vocabulary** (reconciled with existing `tracker.csv` and
`application-tracker` skill):

| status | meaning |
|---|---|
| `shortlisted` | Appears in ranked results; not yet researched |
| `researching` | Company/role research in progress |
| `tailoring` | Resume being tailored for this role |
| `ready` | Resume tailored; ready to apply |
| `applied` | Application submitted; set `applied_date` |
| `interview` | Interview scheduled or underway |
| `offer` | Offer received |
| `rejected` | Terminal — rejected by employer |
| `withdrawn` | Terminal — withdrawn by us |

Fine-grained post-application states live in `outcome` and `notes`:
acknowledgement received, ghosted after 30+ days, offer accepted/declined,
interview stage details, recruiter contact dates. This split is intentional —
`status` drives the pipeline (what skill to run next), `outcome` captures the
final result, `notes` carries the timeline.

---

### Outreach

Represents one cold outreach message to a Person.

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier |
| `person_id` | string | FK to Person |
| `application_id` | string | FK to Application; null if prospecting without a specific job |
| `agency_id` | string | FK to Agency; null if reaching out to company employees |
| `channel` | string | `linkedin`, `email` |
| `direction` | string | `outbound` (we reached out), `inbound` (they contacted us) |
| `message` | string | Draft or sent message text |
| `status` | string | `draft`, `draft_approved`, `sent`, `replied`, `no_response`, `declined`, `connected` |
| `sent_date` | date | When the human sent the message |
| `reply_date` | date | When a reply was received |
| `notes` | string | Outcome, follow-up notes |

**Source:** `cold-outreach` skill (drafts messages, human sends, agent tracks).

**Current storage:** Planned: `data/outreach_tracker.csv`. `.gitignore` covers
`data/outreach_*`.

---

## Storage Strategy

**Today (flat files):**
- `merged_jobs.json` — all discovered jobs (Company + JobDescription embedded)
- `tracker.csv` — Application status (maps to Application table)
- `job_search_preferences.yaml` — standing constraints
- `data/market_state.md` — prose market research
- Application folders — per-job artifacts (jd.md, research.md, CV files)

**Planned (as volume grows):**
- `data/contacts.csv` — Person + Agency data (`.gitignore`d)
- `data/outreach_tracker.csv` — Outreach records (`.gitignore`d)

**Migration trigger:** When querying relationships across files becomes painful
(e.g., "show all outreach for applications at companies in DACH"), consider
SQLite with this schema. Until then, flat files keep the toolchain simple and
agent-friendly.

---

## What's NOT in the model (by design)

- **MarketSnapshot table** — territory viability and market signals are
  narrative. Captured in `data/market_state.md` and referenced in `research.md`.
  Tabular structure adds schema overhead without analytical payoff at our volume.
- **Interviews table** — low volume (single-digit interviews at a time).
  Interview notes live in Application.notes or `applications/<folder>/notes.md`.
- **Documents table** — resume PDFs, cover letters are files in application
  folders. The folder path IS the document reference.
- **Skills / Technologies as entities** — they're list-valued fields on
  JobDescription and the master resume. Normalizing would add join complexity
  for no query we currently run.
