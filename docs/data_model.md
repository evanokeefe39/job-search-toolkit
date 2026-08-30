# Data Model — job-search-toolkit

Single, current authority for the data model across the **warehouse**, the
**tracker event feed**, and the **Twenty CRM**. This doc supersedes the
pre-warehouse model (flat `tracker.csv` / `merged_jobs.json`) and is the
reference for any code, skill, or plan that reads or writes application,
outcome, or job data. When a plan or doc disagrees with this file, this file
wins — update it first, then the plan.

Market research (headwinds/tailwinds, territory viability) stays narrative in
`data/market_state.md`, not tabular.

## Deployment tiers

The same entities exist at every tier, but *where they are stored* and *what is
authoritative* changes. Tiers are additive and opt-in (see
`docs/readme_plan.md`); a user starts low and opts up. **The application folder
slug is the identity that joins every tier.**

| Tier | Name | System of record | What it adds |
|---|---|---|---|
| **T1** | JD ingestion + applying | SQLite + DuckDB warehouse | Scrape → score → shortlist → tailor → apply. Outcome event feed in `data/tracker.db`, mirrored into the warehouse `silver.fact_outcome_event`. No CRM. |
| **T2** | Application funnel + tracker | **Twenty CRM** (authoritative) | Twenty owns the application funnel; the SQLite tracker becomes a sync *cache*, never a fork. Same event protocol, `provenance="twenty"`. |
| **T3** | Full CRM / BD | Twenty + warehouse BD facts | Outreach playbooks, lead scoring, referral tracking, inbound attribution. `silver.dim_person` + `fact_touch` / `fact_referral` / `fact_inbound_attribution`. |

**The one invariant across all tiers:** application state is an **append-only
event feed**, never mutable status. History is preserved; the latest event
derives the current stage. This is the same never-deactivate principle as the
job warehouse.

## Central identity: the application folder slug

Every layer keys an application by its **folder slug**:

```
applications/YYYY-MM-DD_<company-slug>_<role-slug>/
```

- **`status.yaml`** lives in the folder and records the append-only transition
  history.
- **Tracker events** use `folder.name` as `job_id`.
- **Twenty Opportunities** carry the same string in their `folder` field.
- The warehouse `silver.jobs.id` (the source URL) joins *through the folder's
  recorded job URL*, not directly — the `fact_outcome_event.job_id` → `silver.jobs`
  join is **deliberately nullable** (an outcome may reference an application
  whose job was never scraped into the warehouse).

> ⚠️ **Do not assume `job_id` is a URL.** `silver.jobs.id` is the job URL, but
> the tracker's `job_id` is the **folder slug**. They are different keys.

---

## 1. The job warehouse (DuckDB `data/warehouse/jobs.db`)

Kimball star schema, silver = normalized, gold = analytics views. Built by
`pipeline run`. All surrogate keys are SHA-1 hex `[:16]`. See
`src/job_search_toolkit/pipelines/jd/silver.py` / `gold.py`.

### silver.jobs (fact table)

One row per unique `(id, source_board)`, **never deleted**. A job's `id` is its
source URL. Staleness is inferred from `last_seen_at`, never an `is_active` flip.

| Column | Notes |
|---|---|
| `id`, `source_board` | Composite PK. `id` = source URL. |
| `title`, `company`, `location_raw` | Display fields. |
| `source_url`, `apply_url` | Source + application links. |
| `workplace_type`, `salary`, `contract_types`, `seniority_level`, `role_category`, `years_experience_min`, `technologies` | Canonical enrichment fields. |
| `date_posted` | Posting date. |
| `end_client_sector`, `end_client_name`, `engagement_type` | ESN/end-client split (poster ≠ end client in the French market). |
| `overall_score`, `recommendation_tier`, `scores` (JSON) | Score engine output. |
| `company_id` | FK to `dim_company`. |
| `first_seen_run`, `first_seen_at`, `last_seen_run`, `last_seen_at` | Lineage. Jobs never deactivate; staleness = `last_seen_at` older than `STALE_AFTER_DAYS` (60). |
| `is_active` | Stays TRUE once seen (do not use for freshness — use `last_seen_at`). |
| `enriched_at`, `enrichment_version`, `created_at`, `updated_at` | Enrichment/lineage metadata. |

**Enrichment state is column nullability, not flags:** `description_language='fr'`
→ needs translation; empty `technologies` → needs tech extraction.

### silver dimensions

- **`dim_board`** — static: `board_id, name, description_language, base_url`.
- **`dim_company`** — the **golden record**: one row per real-world company,
  regardless of board (deduped in place from the former per
  `(normalized name, source_board)` grain). `company_id` is SHA-1 of the
  normalized name (`[:16]`); `name` is the normalized golden key,
  `display_name` the surviving board's spelling. Enrichment (org_type,
  stock, funding, `news_*`, `insee_*`) lives here — one row per company, so
  enrichment is shared across every board's job rows. `dedup_version` marks
  the derivation (`golden-v1`), so rows stale under a future rule change are
  detectable. Resolution runs as a separate asset (`company_names_resolved`)
  OFF the zero-LLM ranking graph; see `pipelines/jd/company_resolve.py`.
- **`company_alias`** — the source-name → golden-id registry:
  `alias_name` (PK, normalized board-side name), `company_id` (FK to the
  golden row), `source` (`exact|stem|fuzzy|manual|seed|rejected`),
  `confidence` (rapidfuzz score when fuzzy), `created_at`. Append-only;
  `silver.jobs.company_id` is re-keyed through it (one additive backfill,
  idempotent).
- **`dim_date`** — spine over `date_posted`: `date_id, iso_week, month, quarter, year`.

### silver fact tables (append-only)

- **`fact_outcome_event`** — one row per stage transition, copied from the
  tracker feed by the `warehouse_outcomes` asset. Columns: `outcome_event_id`
  (SHA-1 over identity), `job_id` (folder slug; **nullable FK**, not enforced),
  `stage, ts, note, provenance, recorded_at, synced_at`. Idempotent via a
  UNIQUE index on `(job_id, stage, ts, COALESCE(note,''), provenance)`.
- **`fact_touch`** — BD outreach touch: `touch_id, person_id, company_id,
  direction (out|in), channel, playbook, status, event_date, touch_number,
  note, provenance, recorded_at`.
- **`fact_referral`** — `referral_id, referrer_person_id, target_person_id,
  target_company_id, status, event_date, note, provenance, recorded_at`.
- **`fact_inbound_attribution`** — `attribution_id, person_id, company_id,
  source_asset, event_date, note, provenance, recorded_at`.
- **`dim_person`** — `person_id, natural_key, name, linkedin_url, title,
  contact_type, agency, company_id, key_source, follow_up_due_date,
  created_at, updated_at`.

### gold views (`CREATE OR REPLACE` on every run)

`ranked_jobs`, `by_sector`, `by_tier`, `job_history`, `weekly_snapshot`,
`new_this_run`, `disappeared_this_run`, `market_pulse`, `active_recent`,
`score_calibration` (+ BD views: `contact_cadence`, `referral_funnel`,
`inbound_conversion`, `event_funnel`, `next_action`, `relationship`,
`lead_rank`, `lead_score_calibration`).

---

## 2. The tracker event feed (T1: SQLite `data/tracker.db`; T2: Twenty cache)

### protocol.py — the Tracker interface

Backend-agnostic append-only feed. Every backend implements
`record(job_id, stage, ts, note)` / `current(job_id)` / `iter_outcomes()`.
Each event dict:

```python
{job_id, stage, ts, note, provenance, recorded_at}
```

`provenance` is `"sqlite"` (T1) or `"twenty"` (T2). Both backends are drop-in
interchangeable — identical keys, only provenance differs.

### T1 — SQLite backend (`data/tracker.db`)

```sql
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,          -- the application folder slug
    stage       TEXT NOT NULL,
    ts          TEXT NOT NULL,          -- ISO-8601 transition timestamp
    note        TEXT,
    provenance  TEXT NOT NULL DEFAULT 'sqlite',
    recorded_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_events_payload
    ON events(job_id, stage, ts, COALESCE(note, ''));
```

- Append-only; idempotent on exact `(job_id, stage, ts, note)`.
- **`job_id` = the application folder slug** (`folder.name`), NOT a URL.
- A missing/corrupt file is recreated with a warning; a non-empty dir is never
  deleted.

### T2 — Twenty backend

At `tracker.backend = twenty`, **Twenty is authoritative**; `data/tracker.db`
becomes a best-effort SQLite **mirror** (written on `record()`, never a fork).
`iter_outcomes()` reads only from Twenty via `crm-bridge`. A cache write
failure warns but never fails the authoritative record.

The tracker talks to Twenty through the **`crm-bridge`** in the sibling
`../crm` repo (`uv --directory ../crm run crm-bridge`), never directly.

### status.yaml (per application folder)

The folder's append-only transition history — the source both `tracker record`
and the tracker feed derive from, so they cannot diverge.

```yaml
folder: applications/YYYY-MM-DD_company_role
current_stage: applied
created_at: "<ts>"
transitions:
  - {stage: shortlisted, ts: "..."}
  - {stage: tailoring,   ts: "..."}
  - {stage: applied,     ts: "...", note: "source=freework url=... role=... company=..."}
followups: []
```

---

## 3. The Twenty CRM object model (T2/T3, authoritative at T2+)

Defined in `../crm/tasks/plans/twenty-crm-foundation.md`. Standard Twenty
objects repurposed with custom fields (built via the Metadata API):

| Twenty object | Meaning here | Custom fields |
|---|---|---|
| **Company** | Employer, ESN, client, organizer | `companyType` (SELECT), `sector`, `hqCity` |
| **Person** | Recruiter, hiring manager, peer, contact | `contactType` (SELECT), `linkedinUrl` |
| **Opportunity** | One job application | `source` (SELECT), `jobUrl`, `atsScore`, `appliedDate`, `outcome`, `notes`, **`folder`** (the folder slug) |
| **Note** | research.md / jd.md summaries, call notes | attached per opportunity |
| **Task** | follow-ups (tailor, apply-by, follow up) | — |

Custom objects: `Event` (eventRecord), `CreatorTarget`, `SocialPost`, `Mission`,
`PortfolioProject`. Pipeline stages on Opportunity: Shortlisted, Researching,
Tailoring, Ready, Applied, Interview, Offer, Rejected, Withdrawn — these map
1:1 onto the tracker stage vocabulary below.

**Bridge commands** (`../crm`, `crm_bridge/cli.py`): `model`, `seed`, `sync`,
`stats`, `notes`.
- `sync --json` upserts an application and **requires `company` + `role`**
  (structured fields) — it is keyed by `folder` (fallback `jobUrl`).
- `stats` prints funnel statistics (no `--json` flag as of 2026-08-29).

---

## 4. Stage / status vocabulary

The single reconciled vocabulary (in `tracker/protocol.py` `STAGES`, the
Twenty `STAGE_BY_STATUS`, and `status.yaml`). **Status drives the pipeline**;
`outcome` (terminal label) and `note` (timeline detail) carry the rest.

| Stage | Meaning |
|---|---|
| `discovered` | Appeared in results; not yet evaluated. |
| `shortlisted` | In the ranked shortlist; worth an application. |
| `researching` | Company/role research in progress. |
| `tailoring` | Resume being tailored for this role. |
| `ready` | Resume tailored; ready to apply. |
| `applied` | Submitted; record date in `ts` / `applied_date`. |
| `interview` | Interview scheduled/underway. |
| `offer` | Offer received. |
| `rejected` | Terminal — rejected by employer. |
| `withdrawn` | Terminal — withdrawn by us. |
| `ghosted` | Terminal — no response past a reasonable horizon. |

Terminal outcome labels (`outcome` field): `rejected`, `offer_accepted`,
`offer_declined`, `withdrawn`, `ghosted`.

---

## 5. Join map across tiers

```
silver.jobs.id (URL)
      │  (via the application's recorded job URL, not direct FK)
      ▼
applications/<folder>/status.yaml  ←── the folder slug is the join key ──┐
      │                                                                 │
      ▼                                                                 ▼
data/tracker.db events (job_id = folder slug)             Twenty Opportunity (folder = same slug)
      │
      ▼
silver.fact_outcome_event (job_id = folder slug, nullable → silver.jobs)
```

- **folder slug** joins: `status.yaml` ⟷ tracker event ⟷ Twenty Opportunity.
- **`fact_outcome_event.job_id → silver.jobs.id`** is nullable and NOT a FK —
  an application may exist without a scraped job. To reach `silver.jobs` from
  an outcome, resolve the folder's job URL, then join on `id`.

---

## 6. What is NOT in the model (by design)

- **MarketSnapshot** — narrative in `data/market_state.md` / `research.md`.
- **Interviews table** — single-digit volume; lives in application notes.
- **Documents table** — CVs/cover letters are files; the folder path IS the ref.
- **Skills/Technologies as entities** — list-valued fields; normalizing adds
  joins for no query we run.

---

## 7. Storage strategy by tier (current + planned)

| Data | T1 (now) | T2 (opt-in) | T3 (opt-in) |
|---|---|---|---|
| Jobs (canonical + lineage) | `data/warehouse/jobs.db` (silver.jobs + gold) | same | same |
| Application funnel | `data/tracker.db` (SQLite) | **Twenty** (Opportunity), tracker = cache | Twenty |
| Outcome feedback | `silver.fact_outcome_event` | same | same |
| Contacts (Person) | `data/contacts.csv` (planned) | Twenty Person | Twenty Person + `silver.dim_person` |
| Outreach | `data/outreach_tracker.csv` (planned) | Twenty | Twenty + `silver.fact_touch` / `fact_referral` / `fact_inbound_attribution` |
| Leads | — | — | Twenty / `silver.lead` |

All personal/application data is gitignored or in the private `crm` repo — the
toolkit repo is PUBLIC.
