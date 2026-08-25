# Search Configuration Design (2026-08-11)

Design notes for making job discovery configurable per user while keeping the
codebase board-agnostic. Discussion-only — NOT implemented. Captured here so
the design survives until the daily search matrix is built.

Status: approved in conversation; implementation pending. See also the
plugin-framework item in ROADMAP.md Phase 1 (source ingestors).

---

## Principle

**Board properties are immutable declarations in the board adapter; user
preferences are toggles in the user config.** No board-specific knowledge
leaks into user config, and no user-specific values leak into board adapters.

## Three user-config dimensions

All live in `job_search_preferences.yaml` (gitignored, per-user):

| Dimension | Example (Paris user) | Example (Frankfurt user) |
|---|---|---|
| `sources` (enabled boards) | `[freework, hiringcafe]` | `[hiringcafe]` |
| `location.primary` (home) | `Paris, France` | `Frankfurt, Germany` |
| `target_language` | `en` | `de` |

## One immutable board declaration

Each board adapter declares its source language where `description_language`
is set at ingest:

- free-work → `fr`
- HiringCafe → `en`
- future boards → their own declaration

## Search set (list of search definitions)

The daily set is a **flat list of search definitions, each a complete param
set** — NOT a role × geography matrix product. Each row is self-contained:
query, location, contract types, remote types, experience levels, sort,
recency window, radius, and which boards it runs on. Adding a search is
appending a row; editing one is changing a row. A search may use any subset
of params, and rows are independent of each other.

Current default set (8 searches, Paris user):

| # | Query | Geography | Contracts | Boards |
|---|---|---|---|---|
| 1 | data engineer | home | contractor/fixed-term/permanent | both |
| 2 | analytics engineer | home | same | both |
| 3 | data analyst | home | same | both |
| 4 | business intelligence | home | same | both |
| 5 | data engineer | remote-anywhere | same | hiringcafe |
| 6 | analytics engineer | remote-anywhere | same | hiringcafe |
| 7 | data analyst | remote-anywhere | same | hiringcafe |
| 8 | business intelligence | remote-anywhere | same | hiringcafe |

These are the starting instance of the list, not a structural constraint.
Per-board reality: free-work rows need the opaque location slug, HiringCafe
rows use free-text geocoding; a row targets one board or both.

Resolved decisions:

- **"Remote Europe" = remote-anywhere.** A remote search cannot be bounded to
  Europe (geocoder rejects continents; `Europe` resolves to `locality` and
  returns zero results). European remote jobs are a subset of the global
  remote set. Per-country remote is a possible sub-series later.
- **On-site is home-only.** No on-site searches for other cities.
- **US/UK deferred entirely** (2026-08-11). UK needs ~40 pages and a raised
  cap; US needs ~330 pages and is 429-prone — excluded for now.
- **Contract filter is a real narrowing.** Defaults exclude internship,
  temporary, seasonal, volunteer. Confirmed no salary filter exists on either
  board (salary/rate is parsed per-job, never a search constraint).

## Translation stage changes required

Current stage is coupled in four places (all assume fr → en, hiringcafe never
translated):

1. Gate `GATE_TRANSLATE` hardcodes `description_language = 'fr'`
2. `TRANSLATE_SYSTEM` prompt hardcodes French → English
3. Success check hardcodes `!= 'en'` = failure
4. `reset_stale` hardcodes revert to `'fr'`

Required: gate selects rows where `description_language <> target_language`;
prompt templated `{source} -> {target}`; success check and stale reset
target-aware. This makes hiringcafe en→de work for a German user (today it is
silently skipped).

**Open question (schema):** translation currently overwrites `description_text`
— the original is destroyed. Recommendation: preserve original in a new
`description_original` column so re-translation/audit stays honest. Not decided.

## Free-work location slug trap

Free-work location slugs are opaque (`country~region~city~`) and **invalid
slugs silently drop the location filter**, returning the unfiltered board
(observed: bogus slug → broken page misparsed as "27 pages"; valid Paris slug
→ 7 pages). Cannot be derived from a city name — must be discovered from a
browser search URL (the CLI `--url` override is the discovery mechanism).

Required: scraper must verify the location filter actually applied
(result set differs from unfiltered board) — fail fast or warn loudly.

## Deactivation → staleness (resolved)

The old `deactivate_not_seen` marked jobs inactive when absent from the latest
run — a board toggled off would have ALL of its rows deactivated on the next
run, forcing full-run-every-time. Resolved (2026-08-25): jobs are never
deactivated. Staleness is inferred from `last_seen_at` (`STALE_AFTER_DAYS`);
`gold.*` views rank/filter on it, and subset runs (`pipeline run --boards`)
are safe because a board not scraped just stops refreshing `last_seen_at`.

## Territory coverage note

Free-work is France-centric (thin international coverage). A non-French user
gets full HiringCafe coverage but a thin free-work feed. Pipeline must treat
"board produced zero jobs" as normal, not an error.
