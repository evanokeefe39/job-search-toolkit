"""job-search-toolkit — job discovery, enrichment, and application automation.

Package layout:
    job_search_toolkit.scrapers     — board scrapers (free-work, hiringcafe)
    job_search_toolkit.pipeline     — Dagster ETL: bronze -> silver enrichment/scoring
    job_search_toolkit.automation   — human-gated workflows (resume tailoring, …)
    job_search_toolkit.skills       — agent skill definitions (marketplace layout)
    job_search_toolkit.cli          — single entry point: `job-search-toolkit`

Public API: the `job-search-toolkit` console script. Modules are importable for
programmatic use; nothing else is promised stable.
"""

__version__ = "0.1.0"
