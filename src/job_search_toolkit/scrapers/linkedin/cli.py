"""LinkedIn source adapter CLI — ``job-search-toolkit linkedin ...``."""

from __future__ import annotations

import json

import typer

from job_search_toolkit.scrapers.linkedin.adapter import (
    DEFAULT_OUT_DIR,
    run_discovery,
    write_candidate_pool,
)
from job_search_toolkit.scrapers.linkedin.config import LinkedInConfig
from job_search_toolkit.scrapers.linkedin.fetch import fetch_page
from job_search_toolkit.scrapers.linkedin.parse import parse_job, parse_post
from job_search_toolkit.scrapers.linkedin.urls import classify_url

app = typer.Typer(help="LinkedIn source: discover recruiter posts + job listings.", no_args_is_help=True)


@app.command("run")
def linkedin_run(
    backend: str = typer.Option(None, "--backend", help="apify | tavily (overrides config)"),
    country: str = typer.Option(None, "--country", help="search country code (e.g. fr)"),
    language: str = typer.Option(None, "--language", help="search language code (e.g. fr)"),
    tech_list: str = typer.Option(None, "--tech-list", help="path to keyword file (default: built-in list)"),
    out_dir: str = typer.Option(str(DEFAULT_OUT_DIR), "--out", help="candidate pool directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="discover + filter only; do not fetch pages"),
) -> None:
    """Discover, fetch, parse, dedup, and tech-scan LinkedIn posts + jobs.

    Records land in the candidate pool (``--out``) as JSON + CSV. This is the
    human gate: nothing is auto-created in Twenty or the silver warehouse.
    """
    base = LinkedInConfig.from_preferences()
    config = LinkedInConfig(
        backend=(backend or base.backend).lower(),
        country_code=(country or base.country_code).lower(),
        language_code=(language or base.language_code).lower(),
        technology_list=tech_list or base.technology_list,
        post_queries=base.post_queries,
        job_queries=base.job_queries,
    )

    if not config.post_queries and not config.job_queries:
        typer.echo("No LinkedIn queries configured (linkedin.queries in job_search_preferences.yaml).")
        raise typer.Exit(code=1)

    typer.echo(
        f"LinkedIn discovery: backend={config.backend} country={config.country_code} "
        f"language={config.language_code} posts={len(config.post_queries)} jobs={len(config.job_queries)}"
    )

    if dry_run:
        from job_search_toolkit.scrapers.linkedin.discovery import discover, make_backend

        be = make_backend(config.backend)
        for kind, queries in (("post", config.post_queries), ("job", config.job_queries)):
            if not queries:
                continue
            run = discover(list(queries), be, country_code=config.country_code, language_code=config.language_code)
            kept = [r for r in run["results"] if classify_url(r["url"]) == kind]
            typer.echo(f"[{kind}] {len(run['results'])} results, {len(kept)} kept; cost={run['cost_usd']}")
        raise typer.Exit()

    outcome = run_discovery(config)
    paths = write_candidate_pool(outcome, out_dir)

    typer.echo(
        f"posts={len(outcome.posts)} jobs={len(outcome.jobs)} "
        f"stale={len(outcome.stale_urls)} failed={len(outcome.failed_urls)} "
        f"cost_usd={outcome.cost_usd}"
    )
    for kind, path in paths.items():
        typer.echo(f"{kind}: {path}")


@app.command("parse")
def linkedin_parse(url: str) -> None:
    """Fetch + parse a single LinkedIn post or job URL (debugging)."""
    kind = classify_url(url)
    if kind == "drop":
        typer.echo(f"Not a LinkedIn post or job URL: {url}")
        raise typer.Exit(code=1)
    html = fetch_page(url)
    record = parse_post(html, url) if kind == "post" else parse_job(html, url)
    typer.echo(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
