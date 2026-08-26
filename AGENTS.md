# AGENTS.md — job_search_scraping


**Status:** Operational for job-board-to-application workflow. 1 application
(UpClear) at `ready` — submit immediately. ROADMAP.md captures future scope;
do not let roadmap work block submissions. The rule is: if an application is
`ready`, it goes out. Research, tooling, and pipeline improvements happen
between applications, not instead of them.

A multi-board job discovery + application workspace. Scrapers pull
free-work.com and hiringcafe.com listings into a canonical schema; a Dagster
pipeline enriches, scores, and ranks them. Everything after shortlisting is an
interactive, per-role workflow operated by agent skills (`skills/`)
with human review gates — no fully automated ATS pipeline.

## Conventions

- Python 3.14+, `uv` for package management, `.venv` in project root
- Discovery code lives in the installable package `src/job_search_toolkit/` (scrapers, Dagster pipeline, automation). Entry: `job-search-toolkit pipeline run` (dev: `uv run python -m job_search_toolkit.pipelines.jd.run`); legacy `pipelines/jd/_legacy/stage*.py` scripts remain for reference only
- No new dependencies without explicit justification
- Per-application workflow is agent-driven via `skills/<name>/SKILL.md` playbooks (discovered from `skills/` via `.omp/config.yaml`; installed into other harnesses with `job-search-toolkit skills install`) — prose + commands with human gates, never pipeline code
- Application folders: `applications/YYYY-MM-DD_<company>_<role>/` with `inputs/` (`jd.md`, `research.md`, `notes.md`) and `outputs/` (`cv_tailored.yaml`, `cv_tailored.pdf`, `rendercv_output/`); status lives in Twenty CRM (Opportunity — see the application-tracker skill), written via the `crm-bridge` CLI in `../crm`
- PUBLIC repo: `resume/`, `applications/`, `rendercv_output/` are gitignored — never commit personal data or target-company info; application state lives in the private `crm` repo (Twenty), never in this repo
- Dates are `DD/MM/YYYY` (French locale from free-work.com)
- **Resume tailoring is LLM-driven.** `job-search-toolkit tailor run` (CLI in
  `src/job_search_toolkit/cli_tailor.py`) takes `resume/cv.yaml` + a JD and
  produces `cv_tailored.yaml` + `cv_tailored.pdf` via a single DeepSeek call
  with Pydantic-validated structured output through **pydantic-ai**
  (`OpenAIChatModel`; json_mode fallback via `--llm-client json_mode`).
  Config precedence: CLI > env > `tailor_resume_preferences.yaml` (gitignored
  user override; template `tailor_resume_preferences.example.yaml` at repo
  root) > defaults; the package-bundled `TONE.txt` injects tone guidance.
- **Run config is YAML-driven.** Run *mechanics* (timeouts, page sizes, limits,
  LLM rate limits) come from `config.yaml` (gitignored; template
  `config.example.yaml` at repo root) via `src/job_search_toolkit/run_config.py`
  (`RunConfig` + `load_run_config`), selected per-run with
  `pipeline run --config <name>` and `--max-pages N`. Precedence: CLI > named
  run > `defaults` > env fallback > built-in. Search *criteria* (roles,
  locations, LinkedIn queries) stay in `job_search_preferences.yaml`; API
  secrets stay in `.env`. User-facing resume-tailoring preferences live in
  `tailor_resume_preferences.yaml`. Shared resolution/precedence helpers in
  `configutil.py`.
  Resume-Matcher is DEPRECATED (CP1252 mojibake, keyword-padding, 3-page bloat).
  The submission artifact is `cv_tailored.pdf` rendered by RenderCV from LLM-tailored YAML.

## Architecture

```
src/job_search_toolkit/          # Installable PyPI package: `job-search-toolkit`
├── cli.py                       # SINGLE CLI entry point: scrape | pipeline | tailor | skills
├── cli_tailor.py                # Tailor CLI (`job-search-toolkit tailor run`)
├── schemas.py                   # CanonicalJob schema shared by all boards
├── scrapers/                    # Board scrapers (Typer, httpx, bs4 / Next.js data route)
│   ├── freework.py              # free-work.com
│   └── hiringcafe.py            # hiringcafe.com
├── pipelines/                   # Domain pipelines (one sub-package each)
│   └── jd/                      # Dagster 12-asset graph: scrape -> upsert -> incremental enrich -> score -> exports -> gold views
│       ├── definitions.py       # dg.Definitions + ALL_ASSETS
│       ├── silver.py            # DuckDB warehouse: silver.jobs DDL, upsert, enrichment gates
│       ├── assets/              # Asset definitions (one module per stage)
│       │   ├── scrape.py        # freework_jobs, hiringcafe_jobs (writes timestamped bronze + runs.json)
│       │   ├── merge.py         # silver_upsert (bronze -> silver.jobs, ON CONFLICT preserving enrichment)
│       │   ├── enrich.py        # translated, tech_extracted, vertical_classified, company_stats (incremental gates)
│       │   ├── score.py         # scored_jobs (pending rows only), ranked_csv (bridge export)
│       │   ├── gold.py          # gold_views (CREATE OR REPLACE analytics views)
│       │   └── exports.py       # merged_jobs_export, freework_enriched_export (bridge exports)
│       ├── resources/           # Dagster resources
│       │   └── llm_client.py    # LLMClient (async, retry, rate-limit)
│       ├── run.py               # run_pipeline() convenience entry
│       ├── config.py            # Medallion paths + LLM env config
│       ├── gold.py              # DuckDB gold views over silver.jobs
│       ├── enrich_canonical.py, adapt_freework.py, score_engine.py, smoke_utils.py
│       └── _legacy/             # stage*.py — superseded; stage5 promoted to score_engine.py
    └── tailor/                  # Resume tailoring engine (client, prompts, merge, audit, render)
                                # + TONE.txt (package-bundled)

skills/                          # Plugin-standard agent skills (skills/<name>/SKILL.md)
├── jd-refresh/SKILL.md          # Refresh jobs, report delta, stop for shortlist
├── new-application/SKILL.md     # Scaffold application folder + company research
├── tailor-resume/SKILL.md       # `job-search-toolkit tailor run` + human review + RenderCV PDF
├── application-tracker/SKILL.md # Twenty funnel transitions + response-rate stats
├── market-research/SKILL.md     # Multi-level job market trend analysis
└── cold-outreach/SKILL.md       # Find contacts, draft outreach messages

data/                            # Medallion layout (gitignored outputs)
├── bronze/                      # Immutable per-run snapshots: {board}/{iso_timestamp}.json + runs.json
├── silver/                      # Bridge exports materialized from DuckDB: merged_jobs.json, jobs_ranked.csv, freework_jobs_enriched.json
└── warehouse/jobs.db            # DuckDB warehouse: Kimball star schema (silver.dim_board, silver.dim_company, silver.dim_date, silver.jobs fact) + gold.* views
                                # (job-search-toolkit pipeline run builds it; pipeline gold rebuilds views)

.claude-plugin/                  # Plugin manifest + marketplace catalog (Claude Code / OMP)
.omp-plugin/marketplace.json     # OMP-preferred marketplace catalog
.omp/config.yaml                 # Repo agent discovers skills/ via customDirectories
resume/cv.yaml                   # Master resume (RenderCV YAML — gitignored, public repo)
job_search_preferences.yaml      # Job search preferences (location, comp, roles — gitignored)
ROADMAP.md                       # 5-phase roadmap: lead gen → outreach → revenue → learning → analytics
docs/                            # Research and planning
├── ats_resume_knowledge_2026.md # ATS systems, resume writing, prompt injection, job search strategy
├── data_model.md                # 6 entities: Company, JobDescription, Person, Agency, Application, Outreach
├── lead_generation_model.md     # Sales/CRM + DE audit, unified lead sources, qualification model
├── ats_matcher_catalog.md       # ATS tools evaluated (rules reference for pipeline maturation)
├── matcher_contracts.md         # Resume-Matcher API contracts (HISTORICAL — Resume-Matcher deprecated)
└── remote-job-boards.md         # 30+ Europe-focused remote job boards
tasks/lessons.md                 # Session-level lessons log
```

## Quick reference

### Single CLI (job-search-toolkit)

All operations go through one console script (installed with the package):

```bash
job-search-toolkit scrape freework [--format json] [--output data/bronze/freework_jobs.json]
job-search-toolkit scrape hiringcafe [--output data/bronze/hiringcafe_jobs]
job-search-toolkit pipeline run        # ranking path only (scrape -> upsert -> score -> exports -> gold, zero LLM); --enrich runs optional company-research LLM pass
job-search-toolkit pipeline gold       # rebuild gold views over silver.jobs (data/warehouse/jobs.db)
job-search-toolkit tailor run --yaml resume/cv.yaml --jd applications/FOLDER/inputs/jd.md
job-search-toolkit skills install --agent ompy|claude|codex
```

Dev aliases (same code, no install needed):

```bash
uv run python -m job_search_toolkit.pipelines.jd.run   # == job-search-toolkit pipeline run
uv run python -m job_search_toolkit.scrapers.freework --format json
```

The repo's own agent shells need the CLI on PATH — the `.venv` script alone is
not enough. One-time setup: `uv tool install --editable .` (installs to
`~/.local/bin/job-search-toolkit`, kept live against the repo). Skills' `## Requirements`
blocks carry the same instruction for external users.

### Application workflow (agent-driven — submit first, optimize later)

```bash
/skill:jd-refresh           # run discovery, report delta, stop for shortlist
/skill:new-application      # scaffold + research + dealbreaker check + Twenty entry
/skill:tailor-resume        # LLM pipeline: cv.yaml + JD → cv_tailored.yaml → human review → PDF
/skill:application-tracker  # Twenty transitions and response-rate stats
```

**Supporting skills (run between applications, not instead of them):**

```bash
/skill:market-research      # Multi-level job market trend analysis
/skill:cold-outreach        # Find contacts, draft outreach messages
```

**Roadmap skills (not yet built — see ROADMAP.md):**

```bash
/skill:lead-qualification   # Score and prioritize leads across all sources
/skill:event-scout          # Discover and qualify events/conferences/webinars
```

## Engineering practices

- **Use client libraries for APIs — never hand-roll HTTP.** When integrating
  with an API that ships an official client library, use it (e.g. `apify-client`
  for Apify), not raw REST calls. A slug-based REST 404 on a marketplace actor
  usually means the actor isn't in your account yet — verify via the client or
  after adding it, not by assuming the actor is broken. See tasks/lessons.md.


- **Linear history.** No direct commits to `main`. Work on `feat/<name>` branches,
  open a PR, and squash-merge (PR title = commit message). `main` is always the
  sum of merged PRs.
- **PII guard.** This repo is PUBLIC: never commit personal data, target-company
  info, salaries, or API keys (see Conventions). A pre-push hook
  (`scripts/hooks/pre-push`, activated via `git config core.hooksPath scripts/hooks`)
  blocks pushes that touch gitignored PII paths or contain emails / French phone
  numbers / API keys in tracked files. Run `git grep -nE` sweeps before PRs.
- **Packaging.** The wheel bundles `skills/` as package data and registers the
  `job-search-toolkit` console script. After changing package code, verify with
  `uv build` + a clean-venv install of `dist/*.whl`.
- **Data layout.** Data lives in `data/{bronze,silver,warehouse}` (gitignored).
  `data/warehouse/jobs.db` is the DuckDB warehouse (silver.jobs + gold views);
  `data/silver/*` are bridge exports materialized from it — never commit `*.db`
  or scraper outputs.

### Silver warehouse schema (data/warehouse/jobs.db)

Kimball star schema (3 dims + 1 fact):

- **silver.dim_board** — 10 rows, static: board_id, name, description_language, base_url
- **silver.dim_company** — one row per (normalized name, source_board), 1,992 rows:
  company_id (SHA-1 hash), name, display_name, source_board, industry, size_employees,
  year_founded, hq_country, org_type, stock_symbol, stock_exchange, latest_funding_*,
  homepage_url, enriched_at, enrichment_version. LLM research writes here (deferred,
  once per company, never on the ranking path).
- **silver.dim_date** — spine over date_posted: date_id, iso_week, month, quarter, year
- **silver.jobs** — the fact table: one row per unique (id, source_board), never
  deleted. All canonical fields are columns (nested dicts/lists as JSON), plus
  lineage: first_seen_run/first_seen_at, last_seen_run/last_seen_at, is_active,
  enriched_at, enrichment_version, created_at, updated_at, plus company_id FK
  to dim_company. Jobs are never deactivated: ``is_active`` stays TRUE once
  seen, and staleness is inferred from ``last_seen_at`` (see ``STALE_AFTER_DAYS``)
  so subset (``--boards``) runs are safe. The legacy company_info JSON column
  is gone — bridges and fetch_jobs(join_company=True) rebuild it from the dim
  join.

Enrichment state is column nullability, not flags: ``description_language='fr'``
means needs translation; empty ``technologies`` means needs tech extraction.
Company research lives on ``dim_company`` (DIM_COMPANY_GATE: org_type IS NULL),
never on per-row enrichment. ``gold.*`` views: ``ranked_jobs`` (joins dim_company,
excludes stale jobs, exposes ``days_since_posted``/``days_since_seen``),
``by_sector``, ``by_tier``, ``job_history``, ``weekly_snapshot``, ``new_this_run``,
``disappeared_this_run`` (jobs not seen within the staleness horizon).

### Data quality notes

- Most posting companies are French ESN/consulting firms, not end clients
- `end_client_sector` is extracted from descriptions, not company names
- ``dim_company.org_type`` (freework/hellowork/englishjobs/faruse/wwr/remoteok/
  datasciencejobs) is LLM-researched (deferred, per-company); hiringcafe ships
  org_type from the source. ``org_type='unknown'`` means researched and
  inconclusive — not re-researched until the next run's fresh data triggers it.
- 10 companies have unverified stage-4 claims (SearXNG rate-limited during
  research) — these remain in dim_company as researched-once entries.

## Known sharp edges

- **CLI source selection (potential enhancement, 2026-08-25):** the CLI is
  intentionally minimal — `pipeline run [-b BOARD ...]` (default = 9 active
  boards; datasciencejobs opt-in by name), `pipeline ingest --run-id <id>
  [-b board]`, `pipeline list-runs`, `pipeline gold`. A design review
  (3-expert panel) concluded NOT to add YAML config, per-board limit flags, or
  a `--resume` flag. The deferred wins are three robustness micro-fixes
  (`raise_on_error=False` + failed-step reporting, `RetryPolicy` on scrape
  assets, freework `_max_pages()` leak) and, if the tool becomes scheduled or
  multi-user, per-board Dagster partitions. See
  `tasks/plans/cli-source-selection.md` + `ROADMAP.md` → "Potential
  Enhancements". Don't expand the CLI without revisiting that decision.
- **LinkedIn jobs discovery — guest API (2026-08-25):** `linkedin_jobs` now
  discovers via LinkedIn's **public guest jobs API**
  (`linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search`) when
  `guest_jobs: true` in `job_search_preferences.yaml` (default; see
  `scrapers/linkedin/discovery.py` `LinkedInGuestBackend`). It is **free, no auth, and
  yields hundreds of France-scoped job IDs** (location in the card), replacing
  the under-harvesting `apify~google-search-scraper` route for jobs (which now
  only serves `linkedin_posts` + a jobs fallback). **FRAGILE:** the guest API is
  an undocumented public endpoint — LinkedIn can change or block it without
  notice. If it breaks, see **`docs/linkedin-source-spike.md`** for the source
  analysis and quick-fix options (dedicated Apify actors need a purchasable
  actor; google-scraper is the fallback). The France filter
  (`_is_france_job`) still guarantees only `country=FR` jobs enter silver.
- **LinkedIn posts → jobs (2026-08-25):** `scrapers/linkedin/post_extract.py:extract_from_post`
  is the regex verdict gate (`land`/`queue`/`drop`); `queue` rows are reserved
  for an LLM enrichment pass that must be verified to cover `linkedin_posts`.
  Recruiter-region inference (APAC/EMEA/DACH/USA) is unexplored. See
  `tasks/plans/linkedin-posts-to-jobs.md`.
- **Run config (implemented 2026-08-26):** run *mechanics* (timeouts, retries,
  page sizes, limits, LLM rate limits) live in `config.yaml` (gitignored;
  template `config.example.yaml` at repo root) under `defaults:` + named
  `runs:<name>` sections, loaded by `src/job_search_toolkit/run_config.py`
  (`RunConfig` + `load_run_config`). Precedence: CLI > named run > defaults >
  env fallback > built-in. Search *criteria* stay in `job_search_preferences.yaml`;
  secrets stay in `.env`. `pipeline run --config <name> --max-pages N` selects a
  named run / page cap. Shared resolution + precedence helpers live in
  `configutil.py` (reused by the tailor config). Static protocol constants
  (endpoints, status-code sets, regexes) remain in code. See ISSUES.md +
  `tasks/plans/config-cleanup.md`.
- **Reader-mode vs DOM text:** The `read` tool's reader-mode injects artificial "SVG Image" text nodes that don't exist in the BeautifulSoup parse tree. Always test parsers against real `httpx` + `bs4` output, not reader-mode.
- **get_text() concatenation:** BeautifulSoup's `get_text(strip=True)` glues adjacent text nodes with no separators (e.g. `Start dateAs soon as possible`). The `parse_details` regex handles this; new parsers must account for it.
- **Card container:** Job cards are `div.rounded-lg.shadow` containers. The scraper walks up from each `h2` (up to 6 parent levels) until it finds an ancestor whose class list contains both `rounded-lg` and `shadow`.
- **Pagination:** The `?page=N` param is appended to the search URL. Page count is extracted from `N / M` text in the page.
- **French number parsing:** Strip `\u202f` (narrow NBSP), `\xa0` (NBSP), and regular spaces before `float()`. Always split on dash before parsing ranged values.
- **Language detection:** French technical job descriptions are dense with English loanwords — simple word-frequency heuristics fail. Use `fr_count == 0` (any French word → needs translation) rather than ratio thresholds.
- **deepseek-chat alias (probed 2026-08-07):** now served upstream by `deepseek-v4-flash` (0731 revision as of Aug 2026). The pipeline's classification stage still hits malformed function-call JSON (unquoted enum values, double-wrapped arguments); a JSON-repair fallback in `llm_client.py` is the proposed fix. However, `response_format: {"type": "json_object"}` works correctly with v4-flash for prompt-only structured output — Resume-Matcher is confirmed working with this config.
- **deepseek-v4-pro and structured output:** the raw API supports `response_format: {"type": "json_object"}` but v4-pro emits `reasoning_content` that consumes ~65% of the `max_tokens` budget before JSON generation starts. For large structured outputs (resume tailoring), this risks truncation and wastes budget. Use v4-flash for structured output tasks; reserve v4-pro for reasoning-heavy work where the thinking is the deliverable.
- **Resume-Matcher LLM config:** `LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-chat` in `services/docker-compose.yml`. The matcher's `DEFAULT_JSON_MAX_TOKENS` is 8192 (in `llm.py`). If truncation recurs on large resumes, switch to `openai/gpt-5.6-luna` via OpenRouter ($0.10/$0.60, Intel 52.3) or `z-ai/glm-5.2` ($0.206/$0.647, Intel 52.6). Full model comparison in the 2026-08-07 CI log entry below.
- **Resume-Matcher tailoring quality:** the matcher works (confirmed: 5 work experiences preserved, ATS 82.6) but produces surface-level tailoring only — summary rewrites with JD keywords, minor phrasing tweaks to bullets. It does not deeply reframe experience for a target role. The alignment/refinement pass acts as a fabrication guard (validates against master resume) which limits how aggressively the LLM can rewrite. For deeper tailoring, investigate alternative engines (see ISSUES.md closed items). Until then, the matcher provides a useful ATS keyword gap analysis and summary rewrite; bullet-level reframing remains manual.
- **Resume-Matcher master resume requirement:** the improve endpoint's alignment pass validates against a master resume. If the master has unrelated data (e.g., the default `Jane_Doe_CV.pdf` dummy), real work experience gets stripped as "unfabricated." Fix: `PATCH /api/v1/resumes/{master_id}` with real structured data before running improve. Structured data injection via `PATCH` bypasses PDF parsing entirely — field names must match the matcher's schema exactly (`title`, `company`, `years`, `description` as array of strings).
- **RenderCV highlights are `list[str]`:** a literal `" - "` (space-hyphen-space) inside a highlight string becomes a nested sub-bullet (see `process_highlights`); em-dashes are safe. The Hancock clusters in `resume/cv.yaml` use this convention.
- **Windows git-bash has no `/tmp`:** scratch files must go to repo-local gitignored paths (`data/_tmp_*`); POSIX idioms break skill playbooks and scripts.

## Continuous Improvement Log

```
Date: 2026-08-06
Trigger: After completing deep-dive research on 16 ATS resume optimization
         repos/tools, the agent proceeded to reimplement ATSFlow's 30-rule
         scanner as a custom CLI bridge, built a FastAPI orchestrator with
         reimplemented matcher logic, and spent ~2 hours writing code that
         duplicated functionality already available in runnable Docker
         containers — despite the user explicitly saying "use all of them
         in a federated way" and "we shouldn't be reinventing the wheel."
Gap: After research produces a catalog of runnable artifacts (Docker images,
     PyPI packages, CLI tools), no gate existed to force the agent to STOP
     and verify "are these runnable as-is?" before writing implementation
     code. The agent defaulted to builder mode — treating research output
     as a specification to implement against rather than an inventory of
     tools to invoke.
Update: Added rule to tasks/lessons.md: when research produces runnable
        artifacts, the next step is ALWAYS to run them, not reimplement
        them. Only build custom code when the tool has no API/server mode,
        has an incompatible I/O contract, or is unmaintained/broken.
        Before writing any integration code, verify each tool with one
        real request per the External Integration Gate.
```

```
Date: 2026-08-06
Trigger: The federated-ATS reimplementation left a repo full of batch-shaped
         machinery (custom orchestrator, vendored services, test artifacts
         built against a fictional resume) for a workflow that is actually
         interactive and per-role (shortlist -> read JD -> research -> tailor
         -> manual review).
Gap: No principle separated "batch work that automates" (hundreds of items)
     from "judgment work that assists" (one item, human gates).
Update: Pivoted to an application workspace. Agent skills (.agents/skills/)
        are the only orchestration layer; Docker services are ephemeral
        (compose up inside the tailor skill, down after); Resume-Matcher is
        an advisor whose diff log is human-reviewed and applied back into
        RenderCV YAML; the fabrication guard lives in automation/tailor/audit.py;
        personal data is gitignored (repo is PUBLIC). Rule: ask ">50 items or
        judgment on one?" before building automation — judgment steps get
        skills + tools, not pipelines. Full detail in tasks/lessons.md.
```

```
Date: 2026-08-07
Trigger: ISSUES.md reported Resume-Matcher structured-output failures with
         both deepseek-v4-pro ("JSON mode rejected") and deepseek-chat/v4-flash
         ("JSON truncated, unbalanced braces"). Raw API smoke tests (8 models,
         8 providers) proved both models support response_format correctly.
         Resume-Matcher integration test with deepseek-chat succeeded (10s,
         valid JSON, no truncation). The failures were likely transient — the
         deepseek-chat alias may have resolved to an older v4-flash build, and
         the v4-pro rejection was likely a matcher client-detection bug.
Gap: The original test assumed API-level failure without ruling out the
     matcher's client code. The "health check doesn't exercise the LLM path"
     note in ISSUES.md was exactly right — the fix was one raw API call away.
Update: deepseek-chat (v4-flash) is confirmed working for Resume-Matcher.
        v4-pro is disqualified for structured-output tasks because its
        reasoning_content field consumes ~65% of the token budget before JSON
        generation starts. Full model comparison across 8 providers documented
        below. Rule: before declaring an API broken, test it raw — the client
        layer is always a suspect.

Model comparison (2026-08-07, OpenRouter API + direct providers):
Candidates filtered for: released ≤12mo ago, structured_outputs support,
quality parity with v4-pro (Intel ≥38 or Code ≥48), cost ≤2x v4-pro.
Top picks for resume tailoring (writing/editing task, not coding):
  Rank  Model                 Intel  Code   In$/1M  Out$/1M  Released    Notes
  1     deepseek-v4-flash-0731 51.8  69.1   $0.090  $0.180  2026-07-31  Best cost/perf, no reasoning overhead
  2     z-ai/glm-5.2           52.6  68.8   $0.206  $0.647  2026-06-16  Highest quality in budget
  3     openai/gpt-5.6-luna    52.3  71.4   $0.100  $0.600  2026-07-09  GPT-class, strong coding
  4     minimax-m3             45.4  58.6   $0.300  $1.200  2026-05-31  Closest quality parity
  5     deepseek-v4-pro        45.3  59.4   $0.435  $0.870  2026-04-24  Baseline; avoid (reasoning overhead)
Excluded: llama-3.3-70b (>12mo), deepseek-reasoner (rejects response_format),
          codestral (code model), Gemini Flash (3-10x cost), Claude/GPT-5 (>10x).
```
```

```
Date: 2026-08-07
Trigger: Smoke-tested Resume-Matcher's improve endpoint against the UpClear
         Power BI JD. Output confirmed systemic failures: CP1252 mojibake
         (UTF-8 em-dashes decoded as Windows-1252), keyword-padding (appends
         "This project demonstrates BI/data warehousing, Power BI, SQL..."
         to bullets without restructuring), lowercasing of strong verbs
         ("Orchestrated"→"coordinated"), 3-page bloat (16 bullets for one
         role, no pruning), and empty education/skills sections (parser can't
         read RenderCV PDF). The 2026-08-06 decision to use Resume-Matcher
         as the tailoring engine was wrong — it's a builder-first tool that
         treats tailoring as keyword-matching removal, not content reframing.
Gap: Resume-Matcher's architecture (PDF roundtrip, builder-first data model)
     is fundamentally incompatible with our RenderCV YAML workflow.
Update: Resume-Matcher is DEPRECATED. Replacement: scripts/tailor_resume.py —
        a single-script LLM pipeline that reads cv.yaml directly (no PDF
        roundtrip), returns only content fields (not full schema), uses
        Pydantic for structured output validation, and guarantees clean UTF-8
        via yaml.safe_dump(allow_unicode=True). The pipeline completed its
        first successful end-to-end smoke test against the UpClear JD in 14.5s
        with clean audit and valid RenderCV PDF output.
