# free-work job scraper

Scrapes tech/IT job listings from [free-work.com](https://www.free-work.com) into CSV or JSON. Server-rendered HTML — no headless browser needed, no API keys. ~8 seconds for 119 listings across 8 pages.

## Quick start

```bash
git clone https://github.com/evanofslack/job_search_scraping.git
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
