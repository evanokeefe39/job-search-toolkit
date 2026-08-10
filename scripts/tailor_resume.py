"""One-shot resume tailoring CLI (Typer): RenderCV YAML + JD -> tailored YAML -> PDF.

    uv run python scripts/tailor_resume.py \
        --yaml resume/cv.yaml \
        --jd applications/FOLDER/jd.md \
        --output applications/FOLDER/cv_tailored.yaml \
        --level relaxed|moderate|aggressive \
        --tone TONE.txt

Config resolution (highest first): CLI args > env vars > config.yaml > defaults.
See pipeline/tailor/config.py and the gitignored config.yaml at repo root.

SAFETY: The master --yaml file is NEVER modified. merge_content() deep-copies
the original dict; all mutations go to the copy. The --output guard refuses
to overwrite the master path. Without --output, a level-suffixed filename is
auto-generated in the JD's directory.

Levels:
    relaxed   — max 5 bullets per role, ~850 words / ~2 pages (default)
    moderate  — tighter bullets (<=20 words target), condensed skills, ~750 words
    aggressive — Hancock + Modis only, tight bullets, condensed skills, ~600 words

Highlight ranking: impact_first (default — competence/excellence is priority
#1, JD relevance secondary) or jd_relevance. Low-value roles may be cut or
merged (UP3) unless --no-merge-low-value.

Architecture:
    cv.yaml + jd.md -> LLM (pydantic-ai structured output) -> Pydantic-validated
    -> merge -> UP2 sabbatical strip -> audit -> RenderCV PDF.
    Fallback LLM client: json_mode (response_format=json_object) via
    config.yaml `llm_client:` / env LLM_CLIENT / --llm-client.
"""

import asyncio
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer

# Allow importing from pipeline/ when run as a script from repo root.
# FIXME: replace with pip-installable package (pyproject.toml entry points)
#        so scripts/ can import pipeline/ without sys.path manipulation.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from pipeline.tailor import (  # noqa: E402
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
from pipeline.tailor.config import (  # noqa: E402
    TONE_NONE,
    DEFAULT_CONFIG_PATH,
    load_config,
)
from pipeline.tailor.prompts import load_tone  # noqa: E402

app = typer.Typer(no_args_is_help=False, add_completion=False)

_DEFAULT_MASTER = _REPO_ROOT / "resume" / "cv.yaml"


@app.command()
def main(
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
        Path, typer.Option("--config", help="Path to config.yaml")
    ] = DEFAULT_CONFIG_PATH,
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
                     help="Allow cutting/merging low-value roles (default on)"),
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

    # --- Safety: resolve paths, refuse to overwrite master ---
    master_path = cfg.master_yaml.resolve()
    if output is None:
        suffix = f"_{cfg.level}" if cfg.level != "relaxed" else ""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = jd.resolve().parent
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

    # --- Aggressive: filter to most JD-relevant roles before LLM call, and
    # force merge_low_value OFF — the pre-filter already selects the strongest
    # roles deterministically; letting the LLM cut one of them risks it dumping
    # the cut role's bullets under the survivor's header (label-content
    # mismatch that the audit cannot catch). ---
    if cfg.level == "aggressive":
        exp = master["cv"]["sections"].get("experience", [])
        keep_companies = {"hancock", "modis"}
        filtered = [e for e in exp
                    if any(c in str(e.get("company", "")).lower()
                           for c in keep_companies)]
        master["cv"]["sections"]["experience"] = filtered
        cfg.merge_low_value = False
        print(f"[INFO] Aggressive: filtered experience to {len(filtered)} roles "
              f"({', '.join(e.get('company', '?') for e in filtered)})",
              file=sys.stderr)
        print("[INFO] Aggressive: merge_low_value forced OFF (deterministic role set)",
              file=sys.stderr)

    # Deep-copy for audit baseline (before LLM mutates anything)
    original = deepcopy(master)
    cv_text = load_text(master_path)  # full master text for audit/fabrication check

    # --- Pipeline ---
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

    validate_structure(merged, original)
    emit_yaml(merged, output_path)

    if not no_audit:
        hard, jd_adds = check_fabrication(
            original, merged, cv_text, jd_text,
            merge_low_value=cfg.merge_low_value,
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

    if not no_render:
        pdf = render_pdf(output_path)
        print(f"\nDone. {pdf}")
    else:
        print(f"\nDone. {output_path}")
        print("Run: uv run rendercv render " + str(output_path))


if __name__ == "__main__":
    app()
