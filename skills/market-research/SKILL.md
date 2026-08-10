---
name: market-research
description: Multi-level job market trend analysis across territories. Analyzes headwinds and tailwinds at three granularity levels (total market, tech/IT, data engineering) for regions defined in job_search_preferences.yaml. Use when checking market conditions before a job search push, evaluating territory viability, or updating the market context that feeds new-application decisions.
---
## Requirements

Python 3.14+ and uv (package manager) are required. Install the toolkit with:

```bash
pip install job-search-toolkit
```

(or `uv tool install job-search-toolkit`).


# Market Research — multi-level territory analysis

Goal: produce a market conditions report at three granularity levels, scoped to
the regions in `job_search_preferences.yaml`, surfacing headwinds and tailwinds
that affect job search strategy.

## Pre-flight

1. Read `job_search_preferences.yaml` — extract `location.regions`, `roles`,
   `market_research.levels`, and `market_research.signals`.
2. The output is a timestamped markdown file: `data/market_research_YYYY-MM-DD.md`
   (gitignored via `data/` in `.gitignore`). Update a running summary at
   `data/market_state.md` with the latest key findings.

## Research levels (nested — broadest first)

### Level 1: Total Job Market (macro)

Scope: Europe + US (from `market_research.levels[macro].scope`).

Search for and synthesize:
- Overall hiring velocity: total job openings trend, unemployment rates,
  labor force participation changes
- Remote work adoption rates and trends
- Contractor vs permanent employment shifts
- Any macro-level regulatory changes affecting hiring (EU AI Act impacts,
  contractor classification laws, visa/sponsorship policy changes)

Sources: web_search for "europe job market 2026", "US tech hiring trends 2026",
"europe contractor market 2026", "france unemployment rate 2026".

### Level 2: Tech & IT Sector

Scope: France, DACH, Iberia, Europe, US (from `market_research.levels[tech_it].scope`).

Search for and synthesize:
- Tech job openings trend by region
- Salary/rate trends for tech roles
- Layoff activity (company names, dates, affected teams)
- VC funding rounds (early-stage data companies are potential targets)
- New office openings in Paris (expansions = hiring)
- Regulatory changes specific to tech (digital nomad visas, tech worker
  immigration, French auto-entrepreneur/portage rules)

Sources: web_search for "tech layoffs 2026", "france tech job market 2026",
"paris startup funding 2026", "dach tech hiring 2026", "spain tech sector 2026".

### Level 3: Data Engineering & Analytics

Scope: France (Paris + national), DACH, Europe (from `market_research.levels[data_engineering].scope`).

Search for and synthesize:
- Data engineering job openings by region
- Day rates for contract data engineers (France, DACH, Europe)
- Tech stack demand trends (which tools are rising/falling — Fabric, Databricks,
  dbt, Airflow, Spark, etc.)
- Remote adoption for data roles specifically
- Certifications in demand (Azure, AWS, GCP, Databricks)
- Language requirements for data roles in target regions

Sources: web_search for "data engineer day rate france 2026", "data engineering
job market 2026", "azure fabric demand 2026", "data engineer paris salary 2026".

## Headwinds & Tailwinds framework

For each signal in `market_research.signals`, classify as headwind (negative
for job search) or tailwind (positive). Keep a running tally in the output:

| Signal | Classification | Evidence | Source |
|---|---|---|---|
| Company X layoffs | Headwind — more competition | 500 roles cut, Q2 2026 | URL |
| VC fund Y raised $200M | Tailwind — portfolio co's hiring | Early-stage data startups | URL |
| New Paris office (Stripe) | Tailwind — local hiring | 200 engineering roles planned | URL |

## Output format

Write `data/market_research_YYYY-MM-DD.md` with these sections:

### Executive Summary
2-3 sentences: the top finding at each level. Is now a good time to search?

### Level 1: Total Market
### Level 2: Tech & IT
### Level 3: Data Engineering & Analytics
### Headwinds & Tailwinds Table
### Territory Viability Assessment
For each region in `job_search_preferences.yaml`, rate:
- **Viable now** — strong signals, language not a barrier, roles matching preferences
- **Monitor** — some signals but gaps (language, low volume, headwinds)
- **Hold** — significant headwinds or language barrier blocks most roles

### Recommended Actions
Concrete next steps based on findings (e.g., "prioritize DACH remote over
Iberia this quarter", "delay Paris in-office push until Q4", "add German
language learning to timeline").

## Update running state

After writing the timestamped report, update `data/market_state.md`:
- Replace the "Current Assessment" section with the latest executive summary
- Append a dated entry to the "History" section with key metrics
- Keep the file under 200 lines — archive old entries to `data/market_state_archive.md`

## Do not

- Never fabricate market data — every claim needs a source URL
- Never run the discovery pipeline from this skill
- Never modify `job_search_preferences.yaml`
- Never write personal data outside gitignored paths
