"""job-search-toolkit — single CLI entry point for job-search-toolkit.

Subcommands:
    scrape      — board scrapers -> data/bronze/
    pipeline    — Dagster ETL: bronze -> silver (merge, enrich, score)
    application — application workflow & lifecycle
    bd          — BD/CRM: person/touch/referral/inbound records + outreach backfill
    tailor      — resume tailoring automation (human-gated)
    skills      — install agent skills into a harness (omp, claude, codex)

Invocation:
    job-search-toolkit scrape freework --format json --output data/bronze/freework_jobs.json
    job-search-toolkit pipeline run
    job-search-toolkit tailor run --yaml resume/cv.yaml --jd applications/FOLDER/jd.md
    job-search-toolkit skills install --agent ompy
"""
import json
import shutil
from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)

# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------

scrape_app = typer.Typer(help="Scrape job boards into data/bronze/.")

from job_search_toolkit.scrapers import datasciencejobs_app, englishjobs_app, faruse_app, freework_app, hellowork_app, hiringcafe_app, remoteok_app, weworkremotely_app, builtin_app, wttj_app  # noqa: E402

scrape_app.add_typer(datasciencejobs_app, name="datasciencejobs", help="Scrape datasciencejobs.com (data-only board)")
scrape_app.add_typer(faruse_app, name="faruse", help="Scrape faruse.com (English-speaking jobs in Europe)")
scrape_app.add_typer(freework_app, name="freework", help="Scrape free-work.com")

scrape_app.add_typer(englishjobs_app, name="englishjobs", help="Scrape englishjobs.fr")
scrape_app.add_typer(hellowork_app, name="hellowork", help="Scrape hellowork.com")
scrape_app.add_typer(hiringcafe_app, name="hiringcafe", help="Scrape hiringcafe.com")
scrape_app.add_typer(remoteok_app, name="remoteok", help="Scrape remoteok.com")
scrape_app.add_typer(weworkremotely_app, name="wwr", help="Scrape weworkremotely.com")
scrape_app.add_typer(builtin_app, name="builtin", help="Scrape builtin.com/jobs/eu/france (opt-in)")
scrape_app.add_typer(wttj_app, name="wttj", help="Scrape welcometothejungle.com France (opt-in)")
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
    config: str = typer.Option(
        "default", "--config", "-c",
        help="Named run config (runs.<name> in config.yaml) driving timeouts/limits.",
    ),
    max_pages: Optional[int] = typer.Option(
        None, "--max-pages",
        help="Limit each board to N pages (0 = unlimited). Overrides config.yaml max_pages.",
    ),
) -> None:
    """Run the ranking path (scrape -> merge -> score -> export), no LLM.

    With --enrich, the optional LLM enrichment assets run afterwards.
    With --boards (e.g. `--boards linkedin_jobs --boards linkedin_posts`),
    only those boards are scraped and ingested — merge/score/export/gold still
    run on the subset, so a single source can be iterated without re-scraping
    the whole set.
    With --config <name> (runs.<name> in config.yaml) and/or --max-pages N,
    the run's timeouts/limits are taken from that named config / page cap.
    """
    from job_search_toolkit.pipelines.jd.run import run_pipeline

    # Accept `--boards "a b"` or `--boards a --boards b` (split on ws/comma).
    flat = [b for item in (boards or []) for b in item.replace(",", " ").split()]
    ok = run_pipeline(
        enrich=enrich, boards=flat or None, config_name=config, max_pages=max_pages
    )
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



@pipeline_app.command("score-report")
def pipeline_score_report(
    apply_calibration: bool = typer.Option(
        False, "--apply-calibration",
        help="Apply the suggested weights (writes a versioned history entry "
             "and the active-override file). Refuses when there is not enough data.",
    ),
) -> None:
    """Per-feature advance-rate evidence + deterministic weight suggestion.

    Reads the warehouse directly (silver.fact_outcome_event JOIN silver.jobs):
    per feature, the applied -> interview/offer advance rate in the high
    score band vs the low band. Without --apply-calibration nothing changes.
    """
    from job_search_toolkit.pipelines.jd import calibration
    from job_search_toolkit.pipelines.jd.config import WAREHOUSE_DB

    if not WAREHOUSE_DB.is_file():
        print(f"Warehouse not found: {WAREHOUSE_DB}")
        raise typer.Exit(code=1)

    print(f"{'feature':<20} {'low rate':>9} {'low n':>6} {'high rate':>10} {'high n':>7}")
    for feature in calibration.FEATURES:
        ev = calibration.band_evidence(WAREHOUSE_DB, feature)
        print(
            f"{feature:<20} {ev['low_rate']:>9.3f} {ev['low_count']:>6} "
            f"{ev['high_rate']:>10.3f} {ev['high_count']:>7}"
        )

    suggestion = calibration.compute_suggestion(WAREHOUSE_DB)
    if suggestion is None:
        print("not enough data — no calibration")
        if apply_calibration:
            raise typer.Exit(code=1)
        return

    print("\nSuggested weight deltas (SQL-evidenced, deterministic):")
    for feature, delta in suggestion["deltas"].items():
        mark = "+" if delta > 0 else ("-" if delta < 0 else " ")
        print(f"  {feature:<20} {mark}{abs(delta):.2f}")
    print("Renormalized weights:")
    for feature, w in suggestion["weights"].items():
        print(f"  {feature:<20} {w:.4f}")

    if not apply_calibration:
        print("\n(dry run — pass --apply-calibration to apply)")
        return

    result = calibration.apply_calibration(WAREHOUSE_DB)
    print(f"\nApplied calibration v{result['version']}:")
    print(f"  version history: {result['version_file']}")
    print(f"  active weights:  {result['active_file']}")

app.add_typer(pipeline_app, name="pipeline")

@pipeline_app.command("lead-score-report")
def pipeline_lead_score_report(
    apply_calibration: bool = typer.Option(
        False, "--apply-calibration",
        help="Apply the suggested lead weights (gated: requires SQL evidence "
             "from gold.lead_score_calibration).",
    ),
) -> None:
    """Lead-score band distribution + gated weight calibration (Epic 7.2).

    Reads gold.lead_score_calibration: lead-count per lead-score band. Lead
    weights change only via this explicit gated path, promoted from SQL
    evidence — never LLM-proposed. Without outcome-linked advance evidence
    (lead outcomes are deferred), it refuses and writes nothing.
    """
    from job_search_toolkit.pipelines.jd import score_engine
    from job_search_toolkit.pipelines.jd.config import WAREHOUSE_DB

    if not WAREHOUSE_DB.is_file():
        print(f"Warehouse not found: {WAREHOUSE_DB}")
        raise typer.Exit(code=1)

    from job_search_toolkit.pipelines.jd import gold, silver
    con = silver.connect()
    try:
        gold.build_bd_views(con)
        rows = con.execute(
            "SELECT band_start, band_end, lead_count FROM gold.lead_score_calibration"
        ).fetchall()
    finally:
        con.close()
    print(f"{'band':<18} {'leads':>6}")
    for start, end, count in rows:
        print(f"{start:.2f}-{end:.2f} {count:>6}")

    if not apply_calibration:
        print("\n(dry run — pass --apply-calibration to apply)")
        return
    try:
        result = score_engine.lead_apply_calibration(WAREHOUSE_DB)
    except RuntimeError as exc:
        # Lead calibration is outcome-gated (no lead outcomes yet): refuse.
        print(f"not applied — {exc}")
        raise typer.Exit(code=1)
    print(f"\nApplied lead calibration v{result['version']}:")
    print(f"  active weights:  {result['active_file']}")

# ---------------------------------------------------------------------------
# tracker
# ---------------------------------------------------------------------------

tracker_app = typer.Typer(help="Outcome tracker: append-only event feed (SQLite or Twenty).")


@tracker_app.command("record")
def tracker_record(
    job: str = typer.Option(..., "--job", help="Job identifier."),
    stage: str = typer.Option(..., "--stage", help="Stage (e.g. applied, interview)."),
    ts: str = typer.Option(..., "--ts", help="ISO-8601 timestamp of the event."),
    note: str | None = typer.Option(None, "--note", help="Optional free-text note."),
) -> None:
    """Record one outcome event."""
    from job_search_toolkit.tracker import STAGES, get_tracker

    if stage not in STAGES:
        raise typer.BadParameter(
            f"unknown stage {stage!r}; valid stages: {', '.join(STAGES)}"
        )
    try:
        get_tracker().record(job, stage, ts, note)
    except ValueError as exc:  # unknown backend
        raise typer.BadParameter(str(exc)) from exc
    print(f"recorded {job} {stage} @ {ts}")


@tracker_app.command("current")
def tracker_current(
    job: str = typer.Option(..., "--job", help="Job identifier."),
) -> None:
    """Print the latest event for a job, or "none"."""
    from job_search_toolkit.tracker import get_tracker

    cur = get_tracker().current(job)
    if cur is None:
        print("none")
    else:
        print(json.dumps(cur, ensure_ascii=False))


@tracker_app.command("outcomes")
def tracker_outcomes(
    json_out: bool = typer.Option(False, "--json", help="Print a JSON array."),
) -> None:
    """Print all recorded outcome events."""
    from job_search_toolkit.tracker import get_tracker

    events = get_tracker().iter_outcomes()
    if json_out:
        print(json.dumps(events, ensure_ascii=False, indent=2))
    elif not events:
        print("none")
    else:
        for e in events:
            note = f" | {e['note']}" if e.get("note") else ""
            print(f"{e['ts']} | {e['job_id']} | {e['stage']}{note}")


app.add_typer(tracker_app, name="tracker")

# application
# ---------------------------------------------------------------------------

application_app = typer.Typer(help="Application workflow & lifecycle: status.yaml records, follow-ups, per-job reports.")


@application_app.command("record")
def application_record(
    folder: str = typer.Option(..., "--folder", help="Application folder (e.g. applications/YYYY-MM-DD_company_role)."),
    stage: str = typer.Option(..., "--stage", help="Stage from the tracker vocabulary."),
    ts: str = typer.Option(..., "--ts", help="ISO-8601 timestamp of the transition."),
    note: Optional[str] = typer.Option(None, "--note", help="Optional note (source, context, decision)."),
) -> None:
    """Record one outcome: append to the folder's status.yaml AND the tracker feed.

    The folder's status.yaml keeps the append-only transition history; the
    tracker event (keyed on the folder slug) sinks into the warehouse via
    WS1. Both write from the same transition, so they cannot diverge.
    """
    from job_search_toolkit.status import record_outcome

    record_outcome(Path(folder), stage, ts, note=note)
    print(f"recorded {Path(folder).name} -> {stage} @ {ts} (status.yaml + tracker)")


@application_app.command("current")
def application_current(
    folder: str = typer.Option(..., "--folder", help="Application folder."),
) -> None:
    """Print the current stage from the folder's status.yaml (or 'none')."""
    from job_search_toolkit.status import current_stage

    stage = current_stage(Path(folder))
    print(stage if stage else "none")


@application_app.command("followups-due")
def application_followups_due(
    days: int = typer.Option(10, "--days", help="Follow-up threshold in calendar days."),
) -> None:
    """List applications in 'applied' past the follow-up threshold with no outcome.

    Applies the max-two-per-application cap. Drafts are human-sent; this
    tool never sends.
    """
    from job_search_toolkit.followup import MAX_FOLLOWUPS, followups_due
    from job_search_toolkit.tracker import get_tracker

    rows = followups_due(Path("applications"), get_tracker(), days=days)
    if not rows:
        print("No follow-ups due.")
        return
    for r in rows:
        print(
            f"{r['slug']}: applied {r['days_since_applied']}d ago, "
            f"{r['followup_count']}/{MAX_FOLLOWUPS} follow-ups"
        )


@application_app.command("followup-draft")
def application_followup_draft(
    folder: str = typer.Option(..., "--folder", help="Application folder."),
    note: str = typer.Option(..., "--note", help="Draft text (human sends; tool never sends)."),
) -> None:
    """Record a follow-up DRAFT for an application (refused past the cap of 2).

    Drafts are recorded in the folder's status.yaml only — nothing is ever
    sent by the tool; the human sends manually.
    """
    from datetime import UTC, datetime

    from job_search_toolkit.followup import draft_followup

    draft_followup(Path(folder), datetime.now(UTC).isoformat(), note)
    print(f"recorded follow-up draft for {Path(folder).name} (draft-only, human sends)")


@application_app.command("report")
def application_report(
    job_id: str = typer.Option(..., "--job-id", help="silver.jobs id."),
) -> None:
    """Render a deterministic per-job dossier from warehouse features."""
    from job_search_toolkit.pipelines.jd.config import WAREHOUSE_DB
    from job_search_toolkit.report import fetch_job, render_job_report

    job = fetch_job(WAREHOUSE_DB, job_id)
    if job is None:
        raise typer.Exit(f"no job with id {job_id!r} in the warehouse")
    print(render_job_report(job))


app.add_typer(application_app, name="application")

# ---------------------------------------------------------------------------
# bd
# ---------------------------------------------------------------------------

bd_app = typer.Typer(help="BD/CRM: person/touch/referral/inbound records + outreach backfill.")


@bd_app.command("record-touch")
def bd_record_touch(
    person_id: str | None = typer.Option(None, "--person-id", help="dim_person id (optional)."),
    direction: str = typer.Option("out", "--direction", help="Touch direction: out or in."),
    channel: str | None = typer.Option(None, "--channel", help="e.g. linkedin, email."),
    playbook: str | None = typer.Option(None, "--playbook", help="e.g. cold-outreach."),
    status: str = typer.Option(..., "--status", help="drafted/sent/replied/meeting/closed."),
    event_date: str | None = typer.Option(None, "--event-date", help="YYYY-MM-DD (default today)."),
    note: str | None = typer.Option(None, "--note", help="Optional free-text note."),
    provenance: str = typer.Option("sqlite", "--provenance", help="Where this record came from."),
) -> None:
    """Record one touch event."""
    from job_search_toolkit.pipelines.jd import silver

    if status not in silver.BD_TOUCH_STATUS:
        raise typer.BadParameter(
            f"unknown status {status!r}; valid statuses: {', '.join(silver.BD_TOUCH_STATUS)}"
        )
    con = silver.connect()
    try:
        touch_id = silver.record_touch(con, {
            "person_id": person_id,
            "direction": direction,
            "channel": channel,
            "playbook": playbook,
            "status": status,
            "event_date": event_date,
            "note": note,
            "provenance": provenance,
        })
    finally:
        con.close()
    print(touch_id)


@bd_app.command("record-referral")
def bd_record_referral(
    referrer_person_id: str = typer.Option(..., "--referrer-person-id", help="dim_person id of the referrer."),
    target_person_id: str | None = typer.Option(None, "--target-person-id", help="dim_person id of the target (optional)."),
    target_company_id: str | None = typer.Option(None, "--target-company-id", help="dim_company id of the target (optional)."),
    status: str = typer.Option(..., "--status", help="e.g. warm_intro_sent."),
    event_date: str | None = typer.Option(None, "--event-date", help="YYYY-MM-DD (default today)."),
    note: str | None = typer.Option(None, "--note", help="Optional free-text note."),
    provenance: str = typer.Option("sqlite", "--provenance", help="Where this record came from."),
) -> None:
    """Record one referral event."""
    from job_search_toolkit.pipelines.jd import silver

    con = silver.connect()
    try:
        referral_id = silver.record_referral(con, {
            "referrer_person_id": referrer_person_id,
            "target_person_id": target_person_id,
            "target_company_id": target_company_id,
            "status": status,
            "event_date": event_date,
            "note": note,
            "provenance": provenance,
        })
    finally:
        con.close()
    print(referral_id)


@bd_app.command("record-inbound")
def bd_record_inbound(
    person_id: str | None = typer.Option(None, "--person-id", help="dim_person id (optional)."),
    company_id: str | None = typer.Option(None, "--company-id", help="dim_company id (optional)."),
    source_asset: str = typer.Option(..., "--source-asset", help="Source asset that drove the inbound contact."),
    event_date: str | None = typer.Option(None, "--event-date", help="YYYY-MM-DD (default today)."),
    note: str | None = typer.Option(None, "--note", help="Optional free-text note."),
    provenance: str = typer.Option("sqlite", "--provenance", help="Where this record came from."),
) -> None:
    """Record one inbound attribution event."""
    from job_search_toolkit.pipelines.jd import silver

    con = silver.connect()
    try:
        attribution_id = silver.record_inbound(con, {
            "person_id": person_id,
            "company_id": company_id,
            "source_asset": source_asset,
            "event_date": event_date,
            "note": note,
            "provenance": provenance,
        })
    finally:
        con.close()
    print(attribution_id)


@bd_app.command("backfill")
def bd_backfill(
    csv: Path = typer.Option(..., "--csv", help="Legacy data/outreach_tracker.csv to import."),
) -> None:
    """Backfill the legacy outreach CSV into the warehouse (idempotent)."""
    from job_search_toolkit.pipelines.jd import silver

    con = silver.connect()
    try:
        inserted = silver.backfill_outreach_csv(con, csv)
    finally:
        con.close()
    print(f"inserted {inserted} touches from {csv}")


@bd_app.command("cadence")
def bd_cadence() -> None:
    """Print follow-up cadence rows: person_id, name, days_since_last_touch."""
    from job_search_toolkit.pipelines.jd import gold, silver

    con = silver.connect()
    try:
        gold.build_bd_views(con)
        rows = con.execute(
            "SELECT person_id, name, days_since_last_touch FROM gold.contact_cadence"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        print("none")
        return
    for person_id, name, days in rows:
        print(f"{person_id} | {name} | {days}")


@bd_app.command("leads")
def bd_leads(limit: int = typer.Option(20, "--limit", help="Max leads to print.")) -> None:
    """Print scored leads from gold.lead_rank, in lead_score DESC order."""
    from job_search_toolkit.pipelines.jd import gold, silver
    from job_search_toolkit.pipelines.jd import score_engine

    con = silver.connect()
    try:
        silver.ensure_bd_tables(con)
        score_engine.ensure_lead_table(con)
        gold.build_bd_views(con)
        rows = con.execute(
            f"SELECT person_id, company_id, intent, fit, access, urgency, lead_score "
            f"FROM gold.lead_rank LIMIT {int(limit)}"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        print("none")
        return
    print("person_id | company_id | intent | fit | access | urgency | lead_score")
    for person_id, company_id, intent, fit, access, urgency, lead_score in rows:
        print(f"{person_id} | {company_id} | {intent} | {fit} | {access} | {urgency} | {lead_score}")


@bd_app.command("score-leads")
def bd_score_leads() -> None:
    """Score all BD contacts into silver.lead (deterministic, zero-LLM)."""
    from job_search_toolkit.pipelines.jd import silver
    from job_search_toolkit.pipelines.jd import score_engine

    con = silver.connect()
    try:
        silver.ensure_bd_tables(con)
        score_engine.ensure_lead_table(con)
        n = score_engine.score_leads_from_warehouse(con)
    finally:
        con.close()
    print(f"scored {n} leads")


app.add_typer(bd_app, name="bd")

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
