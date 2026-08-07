---
name: new-application
description: Scaffold a new application workspace for a shortlisted job. Input is a row from jobs_ranked.csv or a job URL; locate the full canonical record in merged_jobs.json (fallback freework_jobs_enriched.json), write the JD and research the company — web_search for company and role, an ad-hoc yfinance check in an eval cell (ticker + price trend, pattern in pipeline/stage4_company_stats.py), and a MANUAL Crunchbase checkpoint where the human pastes facts or drops an export — then synthesize research.md from a fixed template and update tracker.csv. Use when starting a new application, preparing to apply, or researching a company before tailoring a CV.
---

# New Application — scaffold + research playbook

Goal: turn one shortlisted job into a complete application workspace:
`applications/YYYY-MM-DD_<company-slug>_<role-slug>/` with `jd.md` and `research.md`,
plus a `tracker.csv` row — ending at a human go/no-go decision.

This skill researches and scaffolds only. It does NOT tailor the CV (see
`tailor-resume`), does NOT run the discovery pipeline, and does NOT apply.

## Conventions (fixed)

- Application folder: `applications/YYYY-MM-DD_<company-slug>_<role-slug>/`
  containing `jd.md`, `research.md`, `cv_tailored.yaml`, `cv_tailored.pdf`, `notes.md`.
  Only `jd.md` and `research.md` are produced here; the rest belong to later skills.
- Slugs: lowercase, hyphens, no accents. Company slug from the company name,
  role slug from the title.
  Example: company "Mon Consultant Indépendant" → `mon-consultant-independant`;
  title "Senior data analyst – CRM" → `senior-data-analyst-crm`.
  Folder: `applications/2026-08-06_mon-consultant-independant_senior-data-analyst-crm/`.
- Tracker: `tracker.csv` (repo root, gitignored). Columns:
  `date_added,company,role,source,url,status,folder,ats_score,applied_date,outcome,notes`.
  Status values: `shortlisted, researching, tailoring, ready, applied, interview,
  offer, rejected, withdrawn`.
- This repo is PUBLIC on GitHub. Personal data goes ONLY in gitignored paths:
  `resume/`, `applications/`, `tracker.csv`. Nothing else may contain personal data.
- Never run `uv run python -m pipeline.run` from this skill; the discovery layer
  is read-only input here.

## Playbook

### 1. Identify the job record

Input is either a row from `jobs_ranked.csv` or a job URL. Resolve it to the full
canonical record:

1. If input is a URL: the record is the one in `merged_jobs.json` whose
   `apply_url` (or `source_url`/`id`) equals the URL. URLs may differ by scheme
   or trailing slash — normalize (`https://` prefix, no trailing slash) before
   comparing.
2. If input is a CSV row: match on the `apply_url` column.
3. Load `merged_jobs.json` in an eval cell and find the record:

```python
import json
jobs = json.load(open("merged_jobs.json", encoding="utf-8"))
# jobs is a list of canonical records; each has id, source_board, source_url,
# title, company, apply_url, location_raw, workplace_type, date_posted, salary,
# contract_types, seniority_level, role_category, years_experience_min,
# technologies, competencies, description_text (English).
target_url = "https://..."  # from the CSV row or the given URL
rec = next(j for j in jobs if (j.get("apply_url") or "").rstrip("/") == target_url.rstrip("/"))
```

4. If no record matches in `merged_jobs.json`, repeat against
   `freework_jobs_enriched.json` (the enriched board export; English description
   is `description_en` there). If it is still not found, STOP and report to the
   human — do not scrape a live page to reconstruct the JD.

### 2. Scaffold the application folder and write jd.md

1. Create the folder per the convention above (today's date, company slug, role
   slug).
2. Write `jd.md` containing the FULL canonical fields, including the English
   `description_text` verbatim. Required sections (mirror the canonical record):

   - Title, company, apply URL, source board, source URL/id
   - Location and workplace type
   - Pay: min/max annual EUR, original currency and frequency, whether disclosed
     (`salary.min_annual_eur`, `salary.max_annual_eur`,
     `salary.currency_original`, `salary.frequency_original`,
     `salary.is_disclosed`)
   - Contract type(s), seniority level, role category, years experience min
   - Technologies, competencies (lists)
   - Full English description (`description_text`; fallback `description_en`
     when the record came from `freework_jobs_enriched.json` — note the source)
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
   `company_stock_symbol` column of `jobs_ranked.csv`, or from `company_stats`
   in `freework_jobs_enriched.json`, or from research. Pattern reference:
   `pipeline/stage4_company_stats.py::_fetch_stock_perf` (already a project
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
   Crunchbase export file into `applications/<folder>/`, then I will synthesize
   them into research.md." Do not proceed to write the Crunchbase portions of
   research.md until the human supplies the facts or file. Never attempt to
   scrape or automate Crunchbase.

### 4. Write research.md from the fixed template

Write `applications/<folder>/research.md` with exactly these sections, each
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
2. If go: ensure a `tracker.csv` row exists for this job with status
   `researching`. If a row already exists (match on `url`), update its
   `status` to `researching` and fill `folder`; otherwise append one. Run in an
   eval cell:

```python
import csv
from pathlib import Path

path = Path("tracker.csv")
row = {
    "date_added": "2026-08-06",  # today
    "company": "Company Name",
    "role": "Role Title",
    "source": "freework",
    "url": "https://...",
    "status": "researching",
    "folder": "applications/2026-08-06_company-slug_role-slug/",
    "ats_score": "", "applied_date": "", "outcome": "", "notes": "",
}
header = ["date_added","company","role","source","url","status","folder",
          "ats_score","applied_date","outcome","notes"]
rows = []
if path.exists():
    with open(path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = rows[0].keys() if rows else header
    for r in rows:
        if r.get("url", "").rstrip("/") == row["url"].rstrip("/"):
            r.update(row)  # keep ats_score/applied_date/outcome if already set
            break
    else:
        rows.append(row)
else:
    fieldnames = header
    rows.append(row)
with open(path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
print(f"tracker.csv: {len(rows)} rows, status={row['status']}")
```

3. If no-go: do not create or keep a tracker row for the job (or mark the
   existing row `withdrawn` if one was already there), and report the decision
   and reason back.

## Failure handling

If any step fails — record not found in either JSON store, web_search returns
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
  `applications/`, `tracker.csv`. This repo is PUBLIC.
- Never auto-apply, auto-tailor, or auto-render: the go/no-go gate (step 5) and
  the `tailor-resume` skill own those.
- Never run the discovery pipeline (`uv run python -m pipeline.run`) — this
  skill only consumes its outputs.
- Never modify anything outside the application folder and `tracker.csv`.
- Never edit or summarize the JD text in `jd.md` — it must stay verbatim.
