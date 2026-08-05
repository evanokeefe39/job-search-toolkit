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
scrape_freework.py          # CLI + scraper (Typer, httpx, bs4)
tasks/lessons.md            # Session-level lessons log
```

Key functions:
- `build_url()` — constructs search URL from CLI params
- `fetch_page()` — HTTP GET with page param
- `parse_details()` — label-anchored regex extracts detail fields from glued text nodes
- `extract_job()` — walks card container DOM to extract all fields
- `scrape()` — pagination loop + CSV/JSON write
- `main()` — Typer CLI command

## Known sharp edges

- **Reader-mode vs DOM text:** The `read` tool's reader-mode injects artificial "SVG Image" text nodes that don't exist in the BeautifulSoup parse tree. Always test parsers against real `httpx` + `bs4` output, not reader-mode.
- **get_text() concatenation:** BeautifulSoup's `get_text(strip=True)` glues adjacent text nodes with no separators (e.g. `Start dateAs soon as possible`). The `parse_details` regex handles this; new parsers must account for it.
- **Card container:** Job cards are `div.rounded-lg.shadow` containers. The scraper walks up from each `h2` (up to 6 parent levels) until it finds an ancestor whose class list contains both `rounded-lg` and `shadow`. There are more `h2`s on the page than job cards (non-job headings exist), so cards that walk up to a non-job container produce invalid results and are skipped.
- **Pagination:** The `?page=N` param is appended to the search URL. Page count is extracted from `N / M` text in the page.
