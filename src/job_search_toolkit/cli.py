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

from job_search_toolkit.scrapers import englishjobs_app, freework_app, hellowork_app, hiringcafe_app  # noqa: E402

scrape_app.add_typer(freework_app, name="freework", help="Scrape free-work.com")
scrape_app.add_typer(englishjobs_app, name="englishjobs", help="Scrape englishjobs.fr")
scrape_app.add_typer(hellowork_app, name="hellowork", help="Scrape hellowork.com")
scrape_app.add_typer(hiringcafe_app, name="hiringcafe", help="Scrape hiringcafe.com")
app.add_typer(scrape_app, name="scrape")

# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

pipeline_app = typer.Typer(help="Enrichment pipeline: bronze -> silver.")


@pipeline_app.command("run")
def pipeline_run() -> None:
    """Run the full DAG: scrape -> merge -> enrich -> score -> export."""
    from job_search_toolkit.pipelines.jd.run import run_pipeline

    ok = run_pipeline()
    if not ok:
        raise typer.Exit(code=1)


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
