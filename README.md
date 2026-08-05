# free-work job scraper

Scrapes tech/IT job listings from [free-work.com](https://www.free-work.com) into CSV or JSON. Server-rendered HTML — no headless browser needed, no API keys. ~8 seconds for 119 listings across 8 pages.

## Quick start

```bash
git clone https://github.com/evanokeefe39/job_search_scraping.git
cd job_search_scraping
uv sync
```

## Usage

```bash
# Default: Paris-area data engineer, all contract types, CSV output
uv run python scrape_freework.py

# JSON output with structured arrays for skills/contract types
uv run python scrape_freework.py -f json

# Custom search
uv run python scrape_freework.py -q "python developer" -c contractor -r full -e senior

# Paste a URL from the browser (overrides all filter flags)
uv run python scrape_freework.py -u "https://www.free-work.com/en-gb/tech-it/jobs?query=devops&..."

# Limit pages, custom output path
uv run python scrape_freework.py -p 3 -o results.csv
```

### Options

| Flag | Description | Default |
|---|---|---|
| `-q, --query` | Keyword search | `data engineer` |
| `-l, --locations` | `country~region~city~` (repeatable) | `fr~ile-de-france~paris~` |
| `-c, --contracts` | `contractor`, `permanent`, `fixed-term` | all |
| `-r, --remote` | `full`, `partial`, `none` | all |
| `-e, --experience` | `junior`, `intermediate`, `senior` | all |
| `-s, --sort` | `date`, `relevance` | `date` |
| `--radius` | Search radius in miles | `30` |
| `-f, --format` | `csv` or `json` | `csv` |
| `-o, --output` | Output file path | `freework_jobs.{csv,json}` |
| `-u, --url` | Full search URL (overrides filters) | — |
| `-p, --max-pages` | Limit pages scraped | all |

## Output

### CSV

14 columns: `title`, `url`, `company`, `company_logo`, `contract_types`, `skills`,
`date_posted`, `start_date`, `duration`, `pay`, `rate`, `remote_type`, `location`,
`description`. List fields are pipe-joined.

### JSON

Same fields as structured types. `contract_types` and `skills` are native arrays,
missing numeric fields are `null`. Writes with `ensure_ascii=False` for French text.

```json
{
  "contract_types": ["Contractor", "Permanent"],
  "title": "Data Hub - Data Engineer",
  "url": "https://www.free-work.com/en-gb/tech-it/job-mission/...",
  "company": "Digistrat consulting",
  "skills": ["IntelliJ IDEA", "Python", "SQL", "XML"],
  "date_posted": "08/05/2026",
  "start_date": "As soon as possible",
  "duration": "3 years",
  "pay": "40k-70k €",
  "rate": "450-600 €",
  "remote_type": "Hybrid",
  "location": "Paris, France",
  "description": "Participation à différents projets de migration..."
}
```

Dates are `DD/MM/YYYY` (French locale). Pay is annual salary (permanent roles);
rate is daily rate in EUR (contractor roles).

## Enrichment pipeline

Five-stage idempotent pipeline that enriches scraped jobs with translations,
tech extraction, sector classification, company research, and multi-dimension scoring.

```bash
# Stage 5 runs without any API key — immediate scored output:
uv run python -m pipeline.stage5_score_analyze --export-csv ranked.csv --top 30

# Full pipeline (requires LLM_API_KEY or DEEPSEEK_API_KEY env var):
uv run python -m pipeline.stage1_translate           # French → English
uv run python -m pipeline.stage2_extract_tech        # Tech stack + competencies
uv run python -m pipeline.stage2b_extract_languages  # Language requirements
uv run python -m pipeline.stage3_classify_vertical   # End-client sector
uv run python -m pipeline.stage4_company_stats       # Company info + stock data
uv run python -m pipeline.stage4b_company_deep_research  # Deep company profiles
uv run python -m pipeline.stage5_score_analyze       # Score + rank

# Every LLM stage supports smoke testing:
uv run python -m pipeline.stage1_translate --smoke 3
```

### Enriched fields

| Field | Stage | Description |
|---|---|---|
| `description_en` | 1 | English translation |
| `extracted_technologies` | 2 | Technologies, tools, platforms |
| `extracted_competencies` | 2 | Domain knowledge, soft skills |
| `seniority_level` | 2 | junior/intermediate/senior/lead |
| `role_category` | 2 | data_engineer/analytics_engineer/etc |
| `language_requirements` | 2b | Required languages + levels |
| `end_client_sector` | 3 | Banking, insurance, energy, etc |
| `engagement_type` | 3 | consulting vs direct hire |
| `company_stats` | 4 | Size, type, stock_performance |
| `company_deep_research` | 4b | Conservative LLM profile |
| `company_verified` | web | Web-verified corrections |
| `scores` | 5 | pay, flexibility, responsibility, tech, company |
| `overall_score` | 5 | Weighted composite |

### Data quality

- Most posting companies are ESN/consulting firms — `end_client_sector` is from descriptions
- `company_stats` reputation claims with `info_quality: "medium"` are LLM inference (unverified unless `company_verified` present)
- Stock performance via yfinance is deterministic and verified
