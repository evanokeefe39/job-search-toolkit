"""One-shot resume tailoring CLI (Typer): RenderCV YAML + JD -> tailored YAML -> PDF.

    job-search-toolkit tailor run \
        --yaml resume/cv.yaml \
        --jd applications/FOLDER/jd.md \
        --output applications/FOLDER/cv_tailored.yaml \
        --level relaxed|moderate|aggressive \
        --tone TONE.txt

Config resolution (highest first): CLI args > env vars > tailor_resume_preferences.yaml > defaults.
See job_search_toolkit/automation/tailor/config.py and tailor_resume_preferences.example.yaml.


SAFETY: the master --yaml file is NEVER modified (merge_content() deep-copies;
the --output guard refuses to overwrite the master path). Aggressive-level role
filtering is deterministic and applied AFTER the LLM merge so an LLM can never
write one company's bullets under another's header; the audit receives the
excluded companies as `exclude_companies` so the cut is policy, not fabrication.
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Annotated, Optional

import typer

from job_search_toolkit.automation.tailor import (
    build_system_prompt,
    build_user_prompt,
    call_llm,
    check_fabrication,
    emit_yaml,
    load_text,
    load_yaml,
    merge_content,
    render_pdf,
    validate_structure,
)
from job_search_toolkit.automation.tailor.config import (
    TONE_NONE,
    DEFAULT_TAILOR_PREFERENCES_PATH,
    load_config,
)
from job_search_toolkit.automation.tailor.prompts import load_tone
from job_search_toolkit.automation.tailor.verify import verify_pdf
from job_search_toolkit.automation.tailor.reviewer import bounded_revise, review_draft


_DEFAULT_MASTER = Path("resume") / "cv.yaml"

app = typer.Typer(no_args_is_help=False, add_completion=False)


# Aggressive mode: keep only these roles (lowercase substring match on company).
_AGGRESSIVE_KEEP = {"hancock", "modis"}


def _aggressive_exclude(master: dict) -> set[str] | None:
    """Lowercase company substrings NOT kept in aggressive mode.

    Derived at runtime from the master so it stays correct if the resume
    changes. Passed to check_fabrication as ``exclude_companies`` so the
    deterministic role cut is policy, not a false fabrication.
    """
    exp = master.get("cv", {}).get("sections", {}).get("experience", [])
    excluded: set[str] = set()
    for e in exp:
        company = str(e.get("company", "")).lower()
        if company and not any(k in company for k in _AGGRESSIVE_KEEP):
            excluded.add(company)
    return excluded or None


def _print_verify_report(report) -> None:
    """Print the verification report lines to stderr."""
    if report.contact_missing:
        print("[VERIFY] Contact missing from text layer:", file=sys.stderr)
        for c in report.contact_missing:
            print(f"  - {c}", file=sys.stderr)
    else:
        print("[VERIFY] Contact literal: OK", file=sys.stderr)
    if report.mojibake:
        print("[VERIFY] Mojibake in text layer:", file=sys.stderr)
        for m in report.mojibake:
            print(f"  - {m}", file=sys.stderr)
    else:
        print("[VERIFY] Glyphs: OK", file=sys.stderr)
    if report.reading_order:
        print("[VERIFY] Reading order:", file=sys.stderr)
        for r in report.reading_order:
            print(f"  - {r}", file=sys.stderr)
    else:
        print("[VERIFY] Reading order: OK", file=sys.stderr)
    observed, target = report.page_count
    print(f"[VERIFY] Page count: {observed} (target <= {target}) "
          f"{'OK' if report.page_count_ok else 'OVER TARGET'}", file=sys.stderr)
    cov = report.keyword_coverage or {}
    covered = cov.get("covered", [])
    supported_missing = cov.get("supported_missing", [])
    genuine_gap = cov.get("genuine_gap", [])
    print(f"[VERIFY] Keyword coverage: covered={len(covered)} "
          f"supported-missing={len(supported_missing)} genuine-gap={len(genuine_gap)}",
          file=sys.stderr)
    if supported_missing:
        print("  supported-missing (in master, absent from PDF): "
              + ", ".join(supported_missing), file=sys.stderr)
    if genuine_gap:
        print("  genuine-gap (absent from master too): "
              + ", ".join(genuine_gap), file=sys.stderr)
    if cov.get("no_signal"):
        print("  (no JD signal — no job description supplied or no terms extracted)",
              file=sys.stderr)
    for p in report.problems:
        print(f"[VERIFY] Problem: {p}", file=sys.stderr)


def _verify_report_text(report) -> str:
    """Short text summary of a VerificationReport for the reviewer prompt."""
    cov = report.keyword_coverage or {}
    lines = [
        f"page_count: {report.page_count[0]} (target <= {report.page_count[1]})"
        + ("" if report.page_count_ok else " OVER TARGET"),
        f"contact_missing: {', '.join(report.contact_missing) or 'none'}",
        f"mojibake: {'; '.join(report.mojibake) or 'none'}",
        f"reading_order: {'; '.join(report.reading_order) or 'OK'}",
        "keyword_coverage: "
        + ", ".join(
            f"{bucket}={', '.join(cov.get(bucket, []) or []) or 'none'}"
            for bucket in ("covered", "supported_missing", "genuine_gap")
        ),
    ]
    lines.extend(report.problems)
    return "\n".join(lines)


@app.command("run")
def run(
    yaml_path: Annotated[
        Optional[Path],
        typer.Option("--yaml", help="Master RenderCV YAML (default: resume/cv.yaml)"),
    ] = None,
    jd: Annotated[
        Path, typer.Option("--jd", help="Path to job description (jd.md)")
    ] = ...,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", help="Tailored YAML output path (auto if omitted)"),
    ] = None,
    level: Annotated[
        Optional[str],
        typer.Option("--level", help="relaxed | moderate | aggressive"),
    ] = None,
    config: Annotated[
        Path, typer.Option("--config", help="Path to tailor_resume_preferences.yaml")
    ] = DEFAULT_TAILOR_PREFERENCES_PATH,
    model: Annotated[
        Optional[str], typer.Option("--model", help="LLM model name (env LLM_MODEL)")
    ] = None,
    base_url: Annotated[
        Optional[str], typer.Option("--base-url", help="OpenAI-compatible base URL")
    ] = None,
    api_key: Annotated[
        Optional[str], typer.Option("--api-key", help="LLM API key (env LLM_API_KEY)")
    ] = None,
    llm_client: Annotated[
        Optional[str],
        typer.Option("--llm-client", help="pydantic_ai | json_mode (env LLM_CLIENT)"),
    ] = None,
    tone: Annotated[
        Optional[Path],
        typer.Option("--tone", help="Tone-of-voice file (plain text, e.g. TONE.txt)"),
    ] = None,
    no_tone: Annotated[
        bool, typer.Option("--no-tone", help="Disable tone guidance entirely")
    ] = False,
    max_highlights: Annotated[
        Optional[int],
        typer.Option("--max-highlights", help="Max bullets kept per role"),
    ] = None,
    highlight_preference: Annotated[
        Optional[str],
        typer.Option(
            "--highlight-preference",
            help="impact_first (default) | jd_relevance",
        ),
    ] = None,
    merge_low_value: Annotated[
        Optional[bool],
        typer.Option("--merge-low-value/--no-merge-low-value",
                     help="Allow cutting low-value roles (default on)"),
    ] = None,
    max_tokens: Annotated[
        Optional[int], typer.Option("--max-tokens", help="LLM max output tokens")
    ] = None,
    temperature: Annotated[
        Optional[float], typer.Option("--temperature", help="LLM temperature")
    ] = None,
    no_render: Annotated[
        bool, typer.Option("--no-render", help="Skip RenderCV PDF")
    ] = False,
    no_audit: Annotated[
        bool, typer.Option("--no-audit", help="Skip fabrication audit")
    ] = False,
    verify_output: Annotated[
        bool, typer.Option("--verify",
                           help="Verify the rendered PDF text layer after render "
                                "(blocks the ready transition on failure)")
    ] = False,
    with_review: Annotated[
        bool, typer.Option("--with-review",
                           help="Bounded drafter-reviewer: one critique + one "
                                "targeted revision after the first pass")
    ] = False,
) -> None:
    """Tailor the master CV to a job description and render the PDF."""
    # --- Resolve config: CLI > env > config.yaml > defaults ---
    cfg = load_config(
        config,
        model=model,
        base_url=base_url,
        api_key=api_key,
        llm_client=llm_client,
        temperature=temperature,
        max_tokens=max_tokens,
        level=level,
        max_highlights=max_highlights,
        highlight_preference=highlight_preference,
        merge_low_value=merge_low_value,
        tone_file=(TONE_NONE if no_tone else
                   str(tone) if tone is not None else None),
        master_yaml=yaml_path,
    )
    if cfg.level == "aggressive":
        # Aggressive fixes the role set deterministically; the LLM must not
        # cut/merge (see index-safety note in module docstring).
        cfg.merge_low_value = False

    # --- Safety: resolve paths, refuse to overwrite master ---
    master_path = cfg.master_yaml.resolve()
    if output is None:
        suffix = f"_{cfg.level}" if cfg.level != "relaxed" else ""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = jd.resolve().parent.parent / "outputs"
        output = out_dir / f"cv_tailored{suffix}_{ts}.yaml"
    output_path = output.resolve()
    if output_path == master_path:
        print("[ERROR] Refusing to overwrite master resume.", file=sys.stderr)
        print(f"  Master: {master_path}", file=sys.stderr)
        print("  Output would overwrite master. Use --output to specify a different path.",
              file=sys.stderr)
        raise typer.Exit(code=1)

    print(f"[INFO] Level:              {cfg.level}", file=sys.stderr)
    print(f"[INFO] Model:              {cfg.model} ({cfg.llm_client})", file=sys.stderr)
    print(f"[INFO] Highlight pref:     {cfg.highlight_preference}", file=sys.stderr)
    print(f"[INFO] Merge low-value:    {cfg.merge_low_value}", file=sys.stderr)
    print(f"[INFO] Master (read-only):  {master_path}", file=sys.stderr)
    print(f"[INFO] Output (write):      {output_path}", file=sys.stderr)
    if cfg.cli_overrides:
        print(f"[INFO] CLI overrides:      {', '.join(cfg.cli_overrides)}",
              file=sys.stderr)

    # --- Load ---
    master = load_yaml(master_path)
    jd_text = load_text(jd)
    tone_text = load_tone(cfg.tone_file)

    # Deep-copy for audit baseline (before LLM mutates anything)
    original = deepcopy(master)
    cv_text = load_text(master_path)  # full master text for audit/fabrication check

    # --- Pipeline (master is NOT pre-filtered — index safety) ---
    system_prompt = build_system_prompt(
        cfg.level,
        max_highlights=cfg.max_highlights,
        tone=tone_text,
        highlight_preference=cfg.highlight_preference,
        merge_low_value=cfg.merge_low_value,
    )
    content = asyncio.run(call_llm(
        system_prompt, build_user_prompt(cv_text, jd_text),
        model_name=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        client_kind=cfg.llm_client,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        max_highlights=cfg.max_highlights,
    ))
    merged = merge_content(
        original, content, jd_text,
        preference=cfg.highlight_preference,
        merge_low_value=cfg.merge_low_value,
        max_highlights=cfg.max_highlights,
    )  # deepcopy — master untouched

    # UP2 policy enforcement (deterministic, not tailoring): the sabbatical
    # is never listed in the experience section (docs/ats_llm_rules.yaml UP2).
    sections = merged["cv"]["sections"]
    exp = sections.get("experience", [])
    kept = [e for e in exp if "sabbatical" not in str(e.get("company", "")).lower()]
    if len(kept) != len(exp):
        sections["experience"] = kept
        print(f"[POLICY] UP2: removed sabbatical from experience "
              f"({len(exp)} -> {len(kept)} entries).", file=sys.stderr)

    # Aggressive: deterministic role filter AFTER merge — the LLM never sees
    # a reindexed resume, so original-index references stay valid throughout.
    if cfg.level == "aggressive":
        exp = sections.get("experience", [])
        filtered = [e for e in exp
                    if any(c in str(e.get("company", "")).lower()
                           for c in _AGGRESSIVE_KEEP)]
        print(f"[INFO] Aggressive: kept {len(filtered)}/{len(exp)} roles "
              f"({', '.join(e.get('company', '?') for e in filtered)})",
              file=sys.stderr)
        sections["experience"] = filtered

    validate_structure(merged, original)
    emit_yaml(merged, output_path)

    if not no_audit:
        hard, jd_adds = check_fabrication(
            original, merged, cv_text, jd_text,
            merge_low_value=cfg.merge_low_value,
            exclude_companies=(_aggressive_exclude(original)
                               if cfg.level == "aggressive" else None),
        )
        if hard:
            print("[AUDIT] HARD fabrications:", file=sys.stderr)
            for h in hard:
                print(f"  - {h}", file=sys.stderr)
        if jd_adds:
            print("[AUDIT] JD-derived (verify with human):", file=sys.stderr)
            for j in jd_adds:
                print(f"  - {j}", file=sys.stderr)
        if not hard and not jd_adds:
            print("[AUDIT] Clean.", file=sys.stderr)

    report_text = ""
    if not no_render:
        pdf = render_pdf(output_path)
        if verify_output or with_review:
            report = verify_pdf(pdf, master, jd_text, cfg.verify_page_target)
            _print_verify_report(report)
            report_text = _verify_report_text(report)
            if verify_output and not report.ok:
                print("[VERIFY] FAIL — this artifact cannot be marked ready.",
                      file=sys.stderr)
                raise typer.Exit(code=1)
        print(f"\nDone. {pdf}")
    else:
        print(f"\nDone. {output_path}")
        print("Run: uv run rendercv render " + str(output_path))
        return

    # --- Bounded drafter-reviewer pass (strictly opt-in) ---
    if with_review and not no_audit and not hard:
        def reviewer(d: str, m: str, j: str, v: str):
            return review_draft(
                d, m, j, v,
                model_name=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key,
                client_kind=cfg.llm_client, temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )

        final, review = bounded_revise(
            original, merged, cv_text, jd_text, reviewer,
            verify_text=report_text,
            merge_kwargs={
                "merge_low_value": cfg.merge_low_value,
                "max_highlights": cfg.max_highlights,
                "exclude_companies": (_aggressive_exclude(original)
                                      if cfg.level == "aggressive" else None),
            },
        )
        if review.changed and review.revision:
            if final is not merged:
                # a revision was applied (bounded_revise returns a NEW dict
                # only when applied)
                print(f"[REVIEW] One targeted revision applied. Critique: {review.critique}",
                      file=sys.stderr)
                merged = final
                emit_yaml(merged, output_path)
                pdf = render_pdf(output_path)  # re-render the revised artifact
                if verify_output:
                    report2 = verify_pdf(pdf, master, jd_text, cfg.verify_page_target)
                    _print_verify_report(report2)
                    if not report2.ok:
                        print("[VERIFY] FAIL after review — cannot mark ready.",
                              file=sys.stderr)
                        raise typer.Exit(code=1)
            else:
                print("[REVIEW] Revision rejected by the fabrication guard — keeping first pass.",
                      file=sys.stderr)
        else:
            print("[REVIEW] No changes — first pass accepted as-is.", file=sys.stderr)


@app.command("verify")
def verify(
    pdf: Annotated[
        Path, typer.Option("--pdf", help="Rendered cv_tailored.pdf to verify")
    ] = ...,
    yaml_path: Annotated[
        Optional[Path],
        typer.Option("--yaml", help="Master RenderCV YAML (default: resume/cv.yaml)"),
    ] = None,
    jd: Annotated[
        Optional[Path],
        typer.Option("--jd", help="Path to job description (jd.md) for keyword coverage"),
    ] = None,
    config: Annotated[
        Path, typer.Option("--config", help="Path to tailor_resume_preferences.yaml")
    ] = DEFAULT_TAILOR_PREFERENCES_PATH,
) -> None:
    """Verify the rendered PDF's text layer: contact literal, glyphs, reading order, page count, keyword coverage."""
    cfg = load_config(config, master_yaml=yaml_path)
    master_path = cfg.master_yaml.resolve()
    master = load_yaml(master_path)
    jd_text = load_text(jd) if jd else ""
    report = verify_pdf(pdf, master, jd_text, cfg.verify_page_target)
    _print_verify_report(report)
    if not report.ok:
        print("[VERIFY] FAIL — this artifact cannot be marked ready.", file=sys.stderr)
        raise typer.Exit(code=1)
    print("[VERIFY] OK — text layer intact.", file=sys.stderr)


if __name__ == "__main__":
    app()
