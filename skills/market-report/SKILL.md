---
name: market-report
description: Generate a data-driven job-market report from the warehouse (posting volumes, skill demand, engagement type, company type, language requirements, seasonality) as a styled HTML/PDF/Markdown deliverable. Ask the user a short set of driving questions (audience, format, time window, scope, depth, theme, delivery) then render a professional report. Use when asked to build a market report, generate a job-market insights page, produce a newsletter/PDF/HTML market summary, or refresh an existing report. Feeds new-application and market-research decisions.
---
## Requirements

Python 3.14+ and `uv`. The warehouse is DuckDB (`data/warehouse/jobs.db`), served via the Quack mirror (`main.jobs`, `main.dim_company`, `main.dim_board`, `main.dim_date`, `main.ranked_jobs`). Chart.js + Tailwind via CDN for the interactive HTML report.

## What this skill does

Turns warehouse data into a polished, professional market report whose shape is driven by a handful of user answers. The current canonical artifact is `data/market_insights_report.html` (dark-mode, interactive charts); this skill generalizes it into a configurable generator.

## Playbook

### 1. Preflight

1. Confirm you are at the repo root (`pwd` → the clone path).
2. Verify the Quack serve mirror is up and reachable:
   - Token and attach: `LOAD quack; CREATE SECRET (TYPE quack, TOKEN '<token>'); ATTACH 'quack:localhost:9494' AS serve; USE serve;`
   - If the server is down, restart it via `hub start` (server: `uv run python data/_quack/server.py`, env `QUACK_TOKEN`). If the warehouse itself is missing, STOP and report — do not fabricate data.
3. Confirm tables exist: `main.jobs` (fact), `main.dim_company`, `main.dim_board`, `main.dim_date`, `main.ranked_jobs`.

### 2. Ask the driving questions (goal: a small set of decisive answers)

Ask the user a SHORT battery that gates the report shape. Present them grouped, with defaults marked, so a user who just wants "the usual report" can accept defaults. **Do not ask all 13 every time** — ask the four that cascade (Q1–Q4), and infer the rest from the audience answer unless the user opts into more.

**Core four (always ask):**

- **Q1 — Audience / use.** *"Who is this report for, and what will you do with it?"*
  - `Executive brief` (headline KPIs, verdict-first, PDF-friendly, sparse) → default time 30/90d, narrative on, format PDF/HTML-summary
  - `Analyst deep-dive` (full tables, per-skill/company detail, interactive HTML) → default 90d, narrative light, HTML interactive
  - `Personal use — my own job hunt` (your roles/territory, action-first takeaways) → default 7/30d, narrative on, HTML
  - `Newsletter / public` (broad, no personal filters, cited, share-friendly standalone) → default 7/30d + seasonality, narrative on, standalone HTML
- **Q2 — Time window.** *"What time window matters?"*
  - `7-day pulse` · `30-day` · `90-day` · `12-month seasonality` · `delta vs last report`
- **Q3 — Data scope.** *"Which slice of the warehouse?"* (multi-select, defaults = all boards, your role family)
  - `All boards` · `named boards` · `named skills/role families` · `named companies/industries` · `a territory/region`
- **Q4 — Delivery format.** *"How will you consume/share it?"*
  - `Interactive HTML` (screen, JS charts) · `PDF` (print/email/archive, static) · `Markdown` (notes/repo/blog) · `Newsletter HTML` (~600px, inline CSS, no JS) · `Chart images` (LinkedIn/X)

**Secondary (ask only if the user wants to tune, or infer from Q1):**
- Q5 — **Depth / granularity:** `Headline KPIs only` · `+ top-10 breakdowns` · `full tables + per-skill/company detail`
- Q6 — **Theme:** `Light` · `Dark` · `Brand (hex codes)` (default Dark — matches the current artifact)
- Q7 — **Layout:** `Landscape / on-screen` · `Portrait / print` (default landscape for HTML, portrait for PDF)
- Q8 — **Density:** `Compact (KPI cards + small multiples)` · `Spacious (one chart per row)`
- Q9 — **Narrative:** `Yes — insights + takeaways` · `Light captions only` · `No — data only`
- Q10 — **Primary surface:** `Charts-first` · `Tables-first` · `Balanced`
- Q11 — **Sections & order** (select/rank): posting volume · skill demand · engagement type · company type · French-language requirements · seasonality · pipeline health (new/stale jobs)
- Q12 — **Interactivity:** `Interactive (hover/sort/collapse)` · `Static` (static is forced for PDF/Markdown/newsletter)
- Q13 — **Cadence:** `One-off` · `Weekly refresh` · `Monthly refresh` (recurring adds a delta-vs-previous section + timestamping/archive)

**Decision rule:** ask Q1–Q4, then set sensible defaults for Q5–Q13 from the Q1 audience answer. If the user says "just make it" or "the usual," use the current `data/market_insights_report.html` as the baseline (dark, interactive, 7/30/90d, all sections) and proceed — do not interrogate.

### 3. Query the warehouse (via Quack serve)

Pull the dimensions the report needs, joining tables in Python (Quack rejects SQL JOINs / `ANY(%s)` — pull separately and join on `company_id`):

- **Volumes (Q2):** `CAST(date_posted AS DATE)` windows — 7/30/90d counts + daily averages + all-time baseline; monthly volume for seasonality. Report the denominator (active jobs with a parseable `date_posted`) and NULL count.
- **Skill demand (Q3):** `technologies` (JSON list) — top skills by count and % of in-scope jobs. Note partial tag coverage.
- **Engagement (Q3):** `contract_types` (JSON list) + daily-rate freelance signal (`salary.frequency_original=='daily'`).
- **Company type (Q3):** derive buckets from `posting_company_type` (end_client/esn), `dim_company.industry/size_employees/year_founded/latest_funding_type`. Mark heuristic buckets `[INFERENCE]`.
- **Language (Q3, France):** **A JD written in French is NOT a hard requirement.** Only explicit text markers are HARD ("français courant", "bilingue français", "langue maternelle", "French required", "fluent French required"); soft = "French proficiency", "français souhaité", "English and French". Buckets: English-friendly / French-written-JD-no-explicit-req / Soft / Hard.
- **Board mix (Q3):** `source_board` distribution. NOTE: datasciencejobs dominates all-time active rows but is a small share of recent-window volume — report both and flag the retention artifact.

**Data quality to surface in the report:**
- NULL `date_posted` (report the count).
- Snapshot bias: the warehouse keeps active postings, so recent windows overstate growth vs the all-time baseline. Mark `[INFERENCE]`.
- englishjobs duplicates (issue #43): per-crawl `sig=` clickout URLs create duplicate rows. Note if it affects counts.

### 4. Build the report

Generate the deliverable per the answers:

- **Interactive HTML (default):** single self-contained file with Tailwind + Chart.js via CDN. Dark theme by default. Sections in the requested order. KPI row up top. Verdict/takeaways callout. Charts for volumes/skills/engagement/company-type/language; tables where requested. Render with `render()` in an eval cell or write the file directly. Save to `data/market_report_<YYYY-MM-DD>_<theme>.html` (or `data/market_insights_report.html` if it's the canonical one).
- **PDF:** same HTML with a `@media print` stylesheet (A4, page breaks, static charts) or headless-Chrome export. Portrait for print.
- **Markdown:** tables + headings; charts become references or are omitted; paste-ready for blog/notes.
- **Newsletter HTML:** ~600px single-column, table-based layout, **inline CSS only, no JS** — charts must be pre-rendered SVG or hosted `<img>`, web-safe fonts, dark-mode-safe colors. Built for Substack/Mailchimp/raw email.
- **Chart images:** export 1–3 key charts (top skills, volume trend) as PNG at social dimensions (LinkedIn 1080×1080 or 1200×627; X 1200×675), with a takeaway headline baked into the image.

Include a short **Methodology / Data caveats** section (denominator, snapshot bias, dedup note) unless the user asked for headline-only.

### 5. Verify and present

1. Open the HTML in the browser (headless) and confirm: all charts render as real Chart.js instances (`Chart.getChart(id)` truthy), sections in order, no broken markup, theme applied. Confirm the KPI numbers match the warehouse queries (spot-check).
2. For PDF/newsletter/image, confirm the constraints hold (static, no JS, correct dimensions).
3. Present a concise summary: what was built, the headline findings (is it a good time to apply, top skills, engagement lean, language barrier, board/company mix), and where the file is. End there — do not auto-apply or auto-tailor.

### 6. Update state

- If this is the canonical market report, refresh `data/market_state.md`'s "Current Assessment" + a dated History entry.
- Write the timestamped report; the interactive HTML is the primary artifact.

## Do not

- Never fabricate market data — every number comes from the warehouse (via Quack) or a cited web source.
- Never treat a French-written JD as a hard language requirement (use the explicit-marker rule).
- Never over-ask: the goal is a decisive short battery, not a survey. Defaults cover the rest.
- Never run the discovery pipeline from this skill (consume its outputs only).
- Never write personal data outside gitignored paths (`resume/`, `applications/`, `data/`).
- Never auto-apply, auto-tailor, or auto-render a CV — this skill produces market context only.
- Never modify pipeline code, schemas, or other skills.

## Failure handling

If Quack is down and cannot be restarted, or the warehouse is missing/unreadable, STOP and report exactly what failed — never invent numbers. If a board/section the user asked for has no data, report it as empty rather than fabricating.

## Relationship to other skills

- `market-research` covers macro headwinds/tailwinds via web search; this skill is the **data-driven** report from the warehouse. They can be combined (this skill's report + market-research's sourced context).
- `jd-refresh` refreshes the funnel; this skill reads its outputs. Run `jd-refresh` first if the user wants freshest data.
