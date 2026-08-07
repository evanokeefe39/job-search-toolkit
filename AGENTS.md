# AGENTS.md — job_search_scraping

## Project summary

A multi-board job discovery + application workspace. Scrapers pull
free-work.com and hiringcafe.com listings into a canonical schema; a Dagster
pipeline enriches, scores, and ranks them. Everything after shortlisting is an
interactive, per-role workflow operated by agent skills (`.agents/skills/`)
with human review gates — no fully automated ATS pipeline.

## Conventions

- Python 3.14+, `uv` for package management, `.venv` in project root
- Discovery code: `scrape_*.py` + `schemas.py` + `pipeline/` (Dagster graph, entry `uv run python -m pipeline.run`); legacy `stage*.py` scripts remain for reference only
- No new dependencies without explicit justification
- Per-application workflow is agent-driven via `.agents/skills/<name>/SKILL.md` playbooks — prose + commands with human gates, never pipeline code
- Application folders: `applications/YYYY-MM-DD_<company>_<role>/` with `jd.md`, `research.md`, `cv_tailored.yaml`, `cv_tailored.pdf`, `notes.md`; status lives in `tracker.csv` (11 columns — see the application-tracker skill)
- PUBLIC repo: `resume/`, `applications/`, `tracker.csv`, `rendercv_output/` are gitignored — never commit personal data or target-company info; gitignore is not retroactive, so new personal paths must be ignored before the first commit
- Dates are `DD/MM/YYYY` (French locale from free-work.com)

## Architecture

```
scrape_freework.py               # free-work.com scraper (Typer, httpx, bs4)
scrape_hiringcafe.py             # hiringcafe.com scraper (Next.js data route)
schemas.py                       # CanonicalJob schema shared by all boards
merged_jobs.json                 # Merged job records (all boards)
jobs_ranked.csv                  # Scored/ranked discovery output

pipeline/                        # Dagster 9-asset graph: scrape -> merge -> enrich -> score -> export
├── assets.py                    # Asset definitions
├── run.py                       # Entry point (`uv run python -m pipeline.run`)
├── config.py, llm_client.py, smoke_utils.py, adapt_freework.py, enrich_canonical.py
└── stage*.py                    # Legacy stage scripts (superseded by assets.py, kept for reference)

.agents/skills/                  # Agent playbooks (oh-my-pi / Claude)
├── jd-refresh/SKILL.md          # Refresh jobs, report delta, stop for shortlist
├── new-application/SKILL.md     # Scaffold application folder + company research
├── tailor-resume/SKILL.md       # Resume-Matcher tailoring, human-reviewed diffs
└── application-tracker/SKILL.md # tracker.csv transitions + response-rate stats

resume/cv.yaml                   # Master resume (RenderCV YAML — gitignored, public repo)
applications/                    # One folder per application (gitignored)
services/docker-compose.yml      # Resume-Matcher only; ephemeral (up/down via tailor-resume)
scripts/audit_alignment.py       # Deterministic fabrication strip (master vs tailored)
docs/                            # Research: ats_matcher_catalog, matcher_contracts, remote-job-boards
tracker.csv                      # Application tracker (gitignored)
tasks/lessons.md                 # Session-level lessons log
```

## Quick reference

### Scraper

```bash
uv run python scrape_freework.py           # default: Paris DE, CSV
uv run python scrape_freework.py -f json   # JSON output
```

### Pipeline (DeepSeek API key required for LLM stages)

```bash
# Full pipeline (canonical path — Dagster graph, all boards):
uv run python -m pipeline.run

# Legacy stage scripts (superseded; smoke-test syntax kept for reference):
uv run python -m pipeline.stage1_translate --smoke 3
uv run python -m pipeline.stage5_score_analyze --export-csv jobs_ranked.csv --top 30
```

### Application workflow (agent-driven)

```bash
/skill:jd-refresh          # run discovery, report delta, stop for shortlist
/skill:new-application     # scaffold applications/<date>_<company>_<role>/, research
/skill:tailor-resume       # Resume-Matcher advisory pass + human-reviewed diff + RenderCV PDF
/skill:application-tracker # tracker.csv transitions and response-rate stats
```

### Enriched JSON schema

Beyond the 14 scraper fields, each job gains:
- `description_en` — English translation (source: deepseek-chat)
- `extracted_technologies`, `extracted_competencies` — structured tech/skill lists
- `seniority_level`, `role_category` — inferred from description
- `language_requirements` — `{languages, work_language, summary}`
- `end_client_sector`, `end_client_name`, `engagement_type` — from description analysis
- `company_stats` — size, type, ticker, stock_performance (yfinance), reputation (LLM — verify)
- `company_deep_research` — conservative LLM profile (stage 4b)
- `company_verified` — web-verified corrections (present on 6 companies)
- `scores` — `{pay, flexibility, low_responsibility, tech_match, company_quality}`
- `overall_score`, `recommendation_tier`

### Data quality notes

- Most posting companies are French ESN/consulting firms, not end clients
- `end_client_sector` is extracted from descriptions, not company names
- `company_stats.reputation_summary` entries with `info_quality: "medium"` are LLM inference — unverified unless `company_verified` is present
- 10 companies have unverified stage-4 claims (SearXNG rate-limited during research)
- Stock data via yfinance is deterministic and verified where present

## Known sharp edges

- **Reader-mode vs DOM text:** The `read` tool's reader-mode injects artificial "SVG Image" text nodes that don't exist in the BeautifulSoup parse tree. Always test parsers against real `httpx` + `bs4` output, not reader-mode.
- **get_text() concatenation:** BeautifulSoup's `get_text(strip=True)` glues adjacent text nodes with no separators (e.g. `Start dateAs soon as possible`). The `parse_details` regex handles this; new parsers must account for it.
- **Card container:** Job cards are `div.rounded-lg.shadow` containers. The scraper walks up from each `h2` (up to 6 parent levels) until it finds an ancestor whose class list contains both `rounded-lg` and `shadow`.
- **Pagination:** The `?page=N` param is appended to the search URL. Page count is extracted from `N / M` text in the page.
- **French number parsing:** Strip `\u202f` (narrow NBSP), `\xa0` (NBSP), and regular spaces before `float()`. Always split on dash before parsing ranged values.
- **Language detection:** French technical job descriptions are dense with English loanwords — simple word-frequency heuristics fail. Use `fr_count == 0` (any French word → needs translation) rather than ratio thresholds.
- **deepseek-chat alias (probed 2026-08-06):** the `deepseek-chat` model alias is now served upstream by `deepseek-v4-flash`, which emits malformed function-call JSON in the classification stage (unquoted enum values, arguments double-wrapped as a string) — validation retries fail and `end_client_*` fields stay empty. Probe the served model before tuning structured-output prompts; a JSON-repair fallback in `llm_client.py` is the proposed fix (pending approval).
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
        RenderCV YAML; the fabrication guard lives in scripts/audit_alignment.py;
        personal data is gitignored (repo is PUBLIC). Rule: ask ">50 items or
        judgment on one?" before building automation — judgment steps get
        skills + tools, not pipelines. Full detail in tasks/lessons.md.
```
