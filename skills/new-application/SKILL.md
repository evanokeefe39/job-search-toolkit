---
name: new-application
description: Scaffold a new application workspace for a shortlisted job. Input is a row from jobs_ranked.csv or a job URL; locate the full canonical record in the warehouse (silver.jobs, active rows), write the JD and research the company — web_search for company and role, an ad-hoc yfinance check in an eval cell (ticker + price trend, pattern in pipelines/jd/_legacy/stage4_company_stats.py), and a MANUAL Crunchbase checkpoint where the human pastes facts or drops an export — then synthesize research.md from a fixed template and update tracker.csv. Use when starting a new application, preparing to apply, or researching a company before tailoring a CV.
---
## Requirements

Python 3.14+ and uv (package manager) are required. Install the toolkit with:
`pip install job-search-toolkit` (or `uv tool install job-search-toolkit`).


# New Application — scaffold + research playbook

Goal: turn one shortlisted job into a complete application workspace:
`applications/YYYY-MM-DD_<company-slug>_<role-slug>/` with `inputs/jd.md` and `inputs/research.md`,
plus a tracker event — ending at a human go/no-go decision.

This skill researches and scaffolds only. It does NOT tailor the CV (see
`tailor-resume`), does NOT run the discovery pipeline, and does NOT apply.

## Conventions (fixed)

- Application folder: `applications/YYYY-MM-DD_<company-slug>_<role-slug>/`
  with `inputs/` (`jd.md`, `research.md`, `notes.md`) and `outputs/`
  (`cv_tailored.yaml`, `cv_tailored.pdf`, `rendercv_output/`).
  Only `inputs/jd.md` and `inputs/research.md` are produced here; the rest belong to later skills.
- Slugs: lowercase, hyphens, no accents. Company slug from the company name,
  role slug from the title.
  Example: company "Mon Consultant Indépendant" → `mon-consultant-independant`;
  title "Senior data analyst – CRM" → `senior-data-analyst-crm`.
  Folder: `applications/2026-08-06_mon-consultant-independant_senior-data-analyst-crm/`.
- Tracker: the repo's `job-search-toolkit tracker` CLI (append-only event
  feed; backend from `config.yaml` `tracker.backend` — local SQLite by
  default, Twenty via config swap). Stage values: `shortlisted, researching,
  tailoring, ready, applied, interview, offer, rejected, withdrawn`.
- This repo is PUBLIC on GitHub. Personal data goes ONLY in gitignored paths:
  `resume/`, `applications/`. Application state lives in the tracker's
  gitignored event feed (`data/tracker.db` by default; Twenty via config) —
  never reference personal data in this repo.
- Never run `job-search-toolkit pipeline run` from this skill; the discovery layer
  is read-only input here.


## Pre-flight

1. Read `job_search_preferences.yaml` — load `location`, `work_arrangement`,
   `employment_type`, `language`, `compensation`, and `roles`. These are
   standing constraints; every job must pass the dealbreaker gate (step 1b)
   before proceeding.
2. If `data/market_state.md` exists, read the "Current Assessment" section —
   it carries the latest territory viability ratings per region. Reference
   it in the Market Context section of research.md.
3. If `data/market_state.md` does not exist, note "market state not yet
   researched" — proceed with the application but flag in research.md that
   market context is pending.

 ## Playbook

### 1. Identify the job record

Input is either a row from `data/silver/jobs_ranked.csv` or a job URL. Resolve it to the full
canonical record by querying the warehouse (all active jobs, both boards):

```python
import duckdb, json
con = duckdb.connect("data/warehouse/jobs.db")
cur = con.execute("SELECT * FROM silver.jobs WHERE is_active")
cols = [d[0] for d in cur.description]
jobs = [dict(zip(cols, r)) for r in cur.fetchall()]
# Nested fields come back as JSON strings — decode the ones this skill reads.
for j in jobs:
    for f in ("salary", "company_info", "scores", "contract_types",
              "technologies", "competencies"):
        if isinstance(j.get(f), str):
            j[f] = json.loads(j[f])
# jobs is a list of canonical records; each has id, source_board, source_url,
# title, company, apply_url, location_raw, workplace_type, date_posted, salary,
# contract_types, seniority_level, role_category, years_experience_min,
# technologies, competencies, description_text (English when enriched).
```

1. If input is a URL: find the record whose `apply_url` (or `source_url`/`id`)
   equals the URL. URLs may differ by scheme or trailing slash — normalize
   (`https://` prefix, no trailing slash) before comparing.
2. If input is a CSV row: match on the `apply_url` column.
3. Find the record in the `jobs` list loaded above:

```python
target_url = "https://..."  # from the CSV row or the given URL
rec = next(j for j in jobs if (j.get("apply_url") or "").rstrip("/") == target_url.rstrip("/"))
```

4. If no record matches, STOP and report to the human — do not scrape a live
   page to reconstruct the JD and do not fall back to stale files. (If the
   job is not in the warehouse's active rows it has expired or is outside the
   current scrape.)

### 1b. Dealbreaker gate (preferences check)

Before scaffolding, check the job record against `job_search_preferences.yaml`.
Auto-reject (STOP, report reason, do not create folder or tracker row) if ANY
of these fire:

1. **Language barrier:** if `language.dealbreakers.hard_french_requirement` is
   set and the JD text (description + requirements) contains phrases like
   "français courant", "maîtrise du français", "bilingue français", "French
   mandatory", "fluent French required", or the entire JD is in French with
   no English version available — STOP: "Rejected: hard French language
   requirement detected."
2. **Employment type mismatch:** if `employment_type.preferred` is
   `freelance_contract` and the job's `contract_types` only contains
   `['cdi', 'permanent']` with no freelance/contract option — flag as
   "Warning: CDI only — preference mismatch" but do NOT auto-reject; present
   to human.
3. **Hybrid location mismatch:** if `work_arrangement.preferred` is `hybrid`
   and `work_arrangement.hybrid_location` is `Paris`, but the job's location
   is outside Paris/Île-de-France with `workplace_type: on_site` — flag as
   "Warning: on-site outside Paris — commute may not be viable" but do NOT
   auto-reject; present to human.

If the job passes all dealbreakers (or only fires warnings), proceed to step 2.
Record any warnings in the go/no-go summary (step 5) so the human sees them
before deciding.
 ### 2. Scaffold the application folder and write jd.md


1. Create the folder structure per the convention: `applications/YYYY-MM-DD_<company-slug>_<role-slug>/inputs/`.

2. Write `inputs/jd.md` containing the FULL canonical fields, including the English
   `description_text` verbatim. Required sections (mirror the canonical record):

   - Title, company, apply URL, source board, source URL/id
   - Location and workplace type
   - Pay: min/max annual EUR, original currency and frequency, whether disclosed
     (`salary.min_annual_eur`, `salary.max_annual_eur`,
     `salary.currency_original`, `salary.frequency_original`,
     `salary.is_disclosed`)
   - Contract type(s), seniority level, role category, years experience min
   - Technologies, competencies (lists)
   - Full English description (`description_text`). If the record's
     `description_language` is `"fr"`, it has not been translated yet — STOP
     and report (the discovery pipeline has not finished enriching this job).
   - Date posted
3. Do not edit or summarize the description; `jd.md` is the verbatim source that
   the `tailor-resume` skill feeds to Resume-Matcher.

### 3. Research loop

Gather company and role intelligence from three sources. Keep notes with source
URLs as you go — they feed research.md.

1. **web_search the company.** Search `"<company name>" company` and
   `<company name> <city/country>`, plus a second search for the role:
   `"<role title>" <company name>`. Record what the company does, its market,
   recent news, and anything about the specific team or posting. Always keep the
   source URLs.
2. **yfinance check (ad-hoc, in an eval cell).** Ticker comes from the
   `company_stock_symbol` column of `data/silver/jobs_ranked.csv`, or from
   `company_info.stock_symbol` in the warehouse record, or from research.
   Pattern reference:
   `src/job_search_toolkit/pipelines/jd/_legacy/stage4_company_stats.py::_fetch_stock_perf` (already a project
   dependency — `yfinance>=0.2.66`). Run in an eval cell:

```python
import yfinance as yf
ticker = "MSFT"  # resolved above; None if the company is private/unknown
if ticker:
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if hist.empty:
        print(f"{ticker}: no price history (likely delisted or wrong ticker)")
    else:
        info = stock.info or {}
        print({
            "ticker": ticker,
            "price_current": round(float(hist["Close"].iloc[-1]), 2),
            "price_52w_high": round(float(hist["Close"].max()), 2),
            "price_52w_low": round(float(hist["Close"].min()), 2),
            "perf_12m_pct": round((float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0]) * 100, 1),
            "market_cap": info.get("marketCap"),
            "currency": info.get("currency", "USD"),
            "data_date": hist.index[-1].strftime("%Y-%m-%d"),
        })
```

   Record the 12-month price trend, 52-week range, market cap, and the data
   date. A private company simply has no ticker — note "private / no ticker"
   and move on; do not guess a ticker.
3. **Crunchbase — MANUAL checkpoint.**
   STOP and present to the human: "Crunchbase is a manual step. Please paste the
   profile facts (founded, funding rounds, headcount, recent news) or drop a
   Crunchbase export file into `applications/<folder>/inputs/`, then I will synthesize
   them into research.md." Do not proceed to write the Crunchbase portions of
   research.md until the human supplies the facts or file. Never attempt to
   scrape or automate Crunchbase.

### 4. Write research.md from the fixed template
Write `applications/<folder>/inputs/research.md` with exactly these sections, each
citing its sources (web_search result URLs, the yfinance data date, the
Crunchbase export filename):

- **Business** — what the company does, products/market, business model, why it
  exists; note where the role sits in the org (if known).
- **Size/Stage** — headcount, revenue scale, public/private, stage (startup /
  scale-up / corporate); from web search and Crunchbase facts.
- **Funding/Financials** — funding rounds and totals (Crunchbase facts), stock
  trend and market cap for public companies (yfinance output with data date),
  or "not disclosed" when unknown.
- **Reputation** — public perception, employee sentiment, press, awards;
  anything that signals how the company treats employees.
- **Red flags** — e.g. declining stock, layoffs in the news, vague JD, long
  "mission" posts, glassdoor noise, missing funding info; an explicit "none
  identified" is acceptable when genuinely nothing surfaced.
- **Market Context** — the territory viability for this role's region from
  `data/market_state.md` (if available). Note: "market state not yet
  researched — run market-research skill" if the file is absent. Include
  relevant headwinds or tailwinds that affect this company/role/region.
- **Headwinds / Tailwinds** — specific signals affecting this application:
  e.g. "Tailwind: company raised Series B in Q1 2026, likely expanding team"
  or "Headwind: parent company announced 10% layoffs in June 2026". Source
  each signal. If no signals found: "No significant market signals identified
  for this company/role."
- **Questions to ask** — 3–6 questions for the interview/recruiter call, derived
  from gaps and red flags above (e.g. team size, project length, pay structure,
  remote policy, churn).
Rules: every factual claim carries a source; mark anything inferred or
unverified as `[INFERENCE]` / `[UNVERIFIED]`; never invent a fact to fill a
section.

### 5. Human go/no-go, then tracker

1. STOP and present to the human: a one-screen summary — company, role, pay
   range, location/workplace, the red flags, and the questions to ask — and ask:
   "Proceed with this application (go) or skip (no-go)?" Do not tailor the CV,
   render PDFs, or apply until the human says go.
2. If go: record the application in the tracker with stage `researching`
   (keyed on the folder slug; append-only and idempotent on identical
   events). Run:

```bash
job-search-toolkit tracker record --job 'applications/YYYY-MM-DD_company-slug_role-slug' --stage 'researching' --ts '<today ISO-8601>' --note 'source=freework url=https://... role="Role Title" company="Company Name"'
```

   Substitute the actual company, role, source, url, and folder slug.

3. If no-go: do not record an event for the job (or record the existing
   record as `withdrawn` via `tracker record` if one was already there), and
   report the decision and reason back.

## Failure handling

If any step fails — record not found in the warehouse, web_search returns
nothing usable, yfinance raises repeatedly, the human is unreachable for the
Crunchbase checkpoint — STOP and report exactly what failed and what was tried.
Do not improvise infrastructure: no live scraping of job boards, no Crunchbase
automation, no alternative research APIs, no pipeline runs.

## Do not

- Never fabricate research findings: no invented funding rounds, headcounts,
  stock figures, or press claims; mark unknowns as unknown.
- Never automate Crunchbase (API, scraper, or browser automation) — it is a
  human-paste/export checkpoint by design.
- Never write personal data outside the gitignored paths: `resume/`,
  `applications/`. This repo is PUBLIC.
- Never auto-apply, auto-tailor, or auto-render: the go/no-go gate (step 5) and
  the `tailor-resume` skill own those.
- Never run the discovery pipeline (`job-search-toolkit pipeline run`) — this
  skill only consumes its outputs.
- Never modify anything outside the application folder (application state is recorded via the tracker CLI).
- Never edit or summarize the JD text in `jd.md` — it must stay verbatim.
