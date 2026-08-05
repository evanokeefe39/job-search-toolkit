# AGENTS.md — job_search_scraping

## Project summary

A CLI scraper for free-work.com tech/IT job listings. Python + httpx + BeautifulSoup.
Server-rendered HTML, no JS/browser needed. Outputs CSV or JSON via Typer CLI.

## Conventions

- Python 3.14+, `uv` for package management, `.venv` in project root
- Single-file script: `scrape_freework.py` — keep it self-contained unless it grows >500 lines
- Dates are `DD/MM/YYYY` (French locale from free-work.com)
- No new dependencies without explicit justification

## Architecture

```
scrape_freework.py              # CLI scraper (Typer, httpx, bs4)
freework_jobs.json              # Raw scraped data (119 jobs)
freework_jobs_enriched.json     # Enriched data (after pipeline)
freework_jobs_ranked.csv        # Scored/ranked CSV export

pipeline/
├── config.py                   # Shared paths, LLM env vars, stage field names
├── llm_client.py               # Async OpenAI-compatible client (DeepSeek by default)
├── smoke_utils.py              # Cost estimation, smoke test support
├── stage1_translate.py         # French → English translation
├── stage2_extract_tech.py      # Technologies, competencies, seniority, role category
├── stage2b_extract_languages.py # Language requirements extraction
├── stage3_classify_vertical.py # End-client sector from descriptions
├── stage4_company_stats.py     # Company info + yfinance stock data
├── stage4b_company_deep_research.py # Deep company profiling
└── stage5_score_analyze.py     # Multi-dimension scoring + ranked CSV export

tasks/lessons.md                # Session-level lessons log
```

## Quick reference

### Scraper

```bash
uv run python scrape_freework.py           # default: Paris DE, CSV
uv run python scrape_freework.py -f json   # JSON output
```

### Pipeline (DeepSeek API key required for stages 1-4b)

```bash
# Smoke test any stage before full run:
uv run python -m pipeline.stage1_translate --smoke 3

# Full pipeline:
uv run python -m pipeline.stage1_translate
uv run python -m pipeline.stage2_extract_tech
uv run python -m pipeline.stage2b_extract_languages
uv run python -m pipeline.stage3_classify_vertical
uv run python -m pipeline.stage4_company_stats
uv run python -m pipeline.stage4b_company_deep_research
uv run python -m pipeline.stage5_score_analyze --export-csv freework_jobs_ranked.csv --top 30

# Stage 5 runs without any API key — works on raw scraped data immediately.
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
