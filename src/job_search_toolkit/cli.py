"""job-search-toolkit — single CLI entry point for job-search-toolkit.

Subcommands:
    scrape      — board scrapers -> data/bronze/
    pipeline    — Dagster ETL: bronze -> silver (merge, enrich, score)
    tailor      — resume tailoring automation (human-gated)
    skills      — install agent skills into a harness (omp, claude, codex)

Invocation:
    job-search-toolkit scrape freework --format json --output data/bronze/freework_jobs.json
    job-search-toolkit pipeline run
    job-search-toolkit tailor run --yaml resume/cv.yaml --jd applications/FOLDER/jd.md
    job-search-toolkit skills install --agent ompy
"""
import shutil
from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)

# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------

scrape_app = typer.Typer(help="Scrape job boards into data/bronze/.")

from job_search_toolkit.scrapers import datasciencejobs_app, englishjobs_app, faruse_app, freework_app, hellowork_app, hiringcafe_app, remoteok_app, weworkremotely_app  # noqa: E402

scrape_app.add_typer(datasciencejobs_app, name="datasciencejobs", help="Scrape datasciencejobs.com (data-only board)")
scrape_app.add_typer(faruse_app, name="faruse", help="Scrape faruse.com (English-speaking jobs in Europe)")
scrape_app.add_typer(freework_app, name="freework", help="Scrape free-work.com")

scrape_app.add_typer(englishjobs_app, name="englishjobs", help="Scrape englishjobs.fr")
scrape_app.add_typer(hellowork_app, name="hellowork", help="Scrape hellowork.com")
scrape_app.add_typer(hiringcafe_app, name="hiringcafe", help="Scrape hiringcafe.com")
scrape_app.add_typer(remoteok_app, name="remoteok", help="Scrape remoteok.com")
scrape_app.add_typer(weworkremotely_app, name="wwr", help="Scrape weworkremotely.com")
app.add_typer(scrape_app, name="scrape")

# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

pipeline_app = typer.Typer(help="Enrichment pipeline: bronze -> silver.")


@pipeline_app.command("run")
def pipeline_run(
    enrich: bool = typer.Option(
        False, "--enrich",
        help="Also run the optional LLM enrichment pass (deferred, dimension-scoped)",
    ),
    boards: list[str] = typer.Option(
        None, "--boards", "-b",
        help="Only run these boards' scrape+ingest (repeatable). Defaults to all active boards.",
    ),
) -> None:
    """Run the ranking path (scrape -> merge -> score -> export), no LLM.

    With --enrich, the optional LLM enrichment assets run afterwards.
    With --boards (e.g. `--boards linkedin_jobs --boards linkedin_posts`),
    only those boards are scraped and ingested — merge/score/export/gold still
    run on the subset, so a single source can be iterated without re-scraping
    the whole set.
    """
    from job_search_toolkit.pipelines.jd.run import run_pipeline

    # Accept `--boards "a b"` or `--boards a --boards b` (split on ws/comma).
    flat = [b for item in (boards or []) for b in item.replace(",", " ").split()]
    ok = run_pipeline(enrich=enrich, boards=flat or None)
    if not ok:
        raise typer.Exit(code=1)


@pipeline_app.command("ingest")
def pipeline_ingest(
    run_id: str = typer.Option(
        ..., "--run-id",
        help="Bronze run id (from runs.json) to ingest without re-scraping",
    ),
    board: str = typer.Option(
        None, "--board", "-b",
        help="Only ingest this board's bronze from the run (default: all boards)",
    ),
) -> None:
    """Recover an orphaned bronze snapshot: ingest run_id -> score -> export -> gold.

    Reads the given run's bronze from ``data/bronze/runs.json`` and upserts it
    into ``silver.jobs``, then runs the score/export/gold assets downstream —
    with NO scrape asset (fully offline). Unknown run ids/boards error listing
    the available ones. See also ``pipeline list-runs``.
    """
    from job_search_toolkit.pipelines.jd.run import run_ingest

    try:
        ok = run_ingest(run_id, board)
    except ValueError as e:
        raise typer.Exit(str(e))
    if not ok:
        raise typer.Exit(code=1)


@pipeline_app.command("list-runs")
def pipeline_list_runs() -> None:
    """List available bronze runs + per-board job counts from runs.json."""
    from job_search_toolkit.pipelines.jd.assets.merge import list_runs

    entries = list_runs()
    if not entries:
        print(
            "No bronze runs found — run `job-search-toolkit pipeline run` "
            "or a scrape first."
        )
        return
    by_run: dict[str, list[tuple[str, int]]] = {}
    for e in entries:
        by_run.setdefault(e.get("run_id", ""), []).append(
            (e.get("board", ""), e.get("job_count", 0))
        )
    for run_id in sorted(by_run):
        boards = ", ".join(f"{b}={n}" for b, n in sorted(by_run[run_id]))
        print(f"{run_id}: {boards}")


@pipeline_app.command("gold")
def pipeline_gold() -> None:
    """Create gold analytics views over the silver warehouse table."""
    from job_search_toolkit.pipelines.jd.config import WAREHOUSE_DB, ensure_data_dirs
    from job_search_toolkit.pipelines.jd.gold import build_gold

    ensure_data_dirs()
    if not WAREHOUSE_DB.exists():
        raise typer.Exit(f"Warehouse not found: {WAREHOUSE_DB}. Run `job-search-toolkit pipeline run` first.")
    build_gold(WAREHOUSE_DB)
    print(f"Gold views rebuilt: {WAREHOUSE_DB}")


app.add_typer(pipeline_app, name="pipeline")

# ---------------------------------------------------------------------------
# tailor
# ---------------------------------------------------------------------------

from job_search_toolkit.cli_tailor import app as tailor_app  # noqa: E402

app.add_typer(tailor_app, name="tailor", help="Resume tailoring automation.")

# ---------------------------------------------------------------------------
# linkedin
# ---------------------------------------------------------------------------

from job_search_toolkit.scrapers.linkedin.cli import app as linkedin_app  # noqa: E402

app.add_typer(linkedin_app, name="linkedin", help="Discover LinkedIn recruiter posts + job listings.")

# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------

skills_app = typer.Typer(help="Install agent skills into a harness.")

# Canonical skill roots, in resolution order (see skills/README.md).
_SKILL_ROOTS = (
    Path(__file__).resolve().parent / "skills",   # bundled in the wheel
    Path.cwd() / "skills",                        # repo checkout
)

# Harness skill directories (user level).
_HARNESS_DIRS = {
    "ompy": Path.home() / ".agents" / "skills",
    "claude": Path.home() / ".claude" / "skills",
    "codex": Path.home() / ".codex" / "skills",
}


def _find_skills_root() -> Path | None:
    for root in _SKILL_ROOTS:
        if root.is_dir() and any(root.glob("*/SKILL.md")):
            return root
    return None


@skills_app.command("install")
def skills_install(
    agent: Annotated[
        str, typer.Option("--agent", "-a", help="Target harness: ompy | claude | codex")
    ] = "ompy",
    target: Annotated[
        Optional[Path],
        typer.Option("--target", help="Explicit destination dir (overrides --agent)"),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite existing skill dirs")
    ] = False,
) -> None:
    """Copy packaged skills into the target harness's skill directory.

    Requires `job-search-toolkit` to be installed (pip/uv) so the CLI exists;
    skills themselves are plain SKILL.md playbooks that call this CLI.
    """
    src = _find_skills_root()
    if src is None:
        raise typer.Exit(
            "No skills found: neither the package bundle nor ./skills exists. "
            "Install from a repo checkout or reinstall the wheel."
        )

    dest = target
    if dest is None:
        dest = _HARNESS_DIRS.get(agent)
        if dest is None:
            raise typer.Exit(f"Unknown agent {agent!r}; use --target to set the dir.")
    dest.mkdir(parents=True, exist_ok=True)

    installed = 0
    for skill_dir in sorted(src.iterdir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        out = dest / skill_dir.name
        if out.exists() and not force:
            print(f"skip  {skill_dir.name} (exists; use --force to overwrite)")
            continue
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(skill_dir, out)
        installed += 1
        print(f"installed {skill_dir.name} -> {out}")

    print(f"\n{installed} skills installed to {dest}")
    if agent == "ompy":
        print("Restart oh-my-pi or run /reload for discovery to pick them up.")


app.add_typer(skills_app, name="skills", help="Install agent skills into a harness.")


def main() -> None:
    app()


if __name__ == "__main__":
    app()
