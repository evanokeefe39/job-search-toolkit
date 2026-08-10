"""One-shot resume tailoring: RenderCV YAML + JD -> tailored RenderCV YAML -> PDF.

    uv run python scripts/tailor_resume.py \
        --yaml resume/cv.yaml \
        --jd applications/FOLDER/jd.md \
        --output applications/FOLDER/cv_tailored.yaml \
        --level relaxed|moderate|aggressive

SAFETY: The master --yaml file is NEVER modified. merge_content() deep-copies
the original dict; all mutations go to the copy. The --output guard refuses
to overwrite the master path. Without --output, a level-suffixed filename is
auto-generated in the JD's directory.

Levels:
    relaxed   — max 5 bullets per role, ~850 words / ~2 pages (default)
    moderate  — tighter bullets (<=20 words target), condensed skills, ~750 words
    aggressive — Hancock + Modis only, tight bullets, condensed skills, ~600 words

Architecture:
    cv.yaml + jd.md -> LLM (json_mode) -> Pydantic-validated -> merge -> yaml -> rendercv PDF
"""

import argparse
import asyncio
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

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

_DEFAULT_MASTER = _REPO_ROOT / "resume" / "cv.yaml"


def main():
    p = argparse.ArgumentParser(description="One-shot resume tailoring")
    p.add_argument("--yaml", type=Path, default=_DEFAULT_MASTER,
                   help=f"Master RenderCV YAML (default: {_DEFAULT_MASTER})")
    p.add_argument("--jd", required=True, type=Path,
                   help="Path to job description (jd.md)")
    p.add_argument("--output", type=Path, default=None,
                   help="Tailored YAML output path (auto-generated if omitted)")
    p.add_argument("--level", choices=["relaxed", "moderate", "aggressive"],
                   default="relaxed",
                   help="Bullet-count aggressiveness (default: relaxed)")
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--no-audit", action="store_true")
    args = p.parse_args()

    # --- Safety: resolve paths, refuse to overwrite master ---
    master_path = args.yaml.resolve()
    if args.output is None:
        suffix = f"_{args.level}" if args.level != "relaxed" else ""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = args.jd.resolve().parent
        args.output = out_dir / f"cv_tailored{suffix}_{ts}.yaml"
    output_path = args.output.resolve()
    if output_path == master_path:
        print("[ERROR] Refusing to overwrite master resume.", file=sys.stderr)
        print(f"  Master: {master_path}", file=sys.stderr)
        print("  Output would overwrite master. Use --output to specify a different path.",
              file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Level:              {args.level}", file=sys.stderr)
    print(f"[INFO] Master (read-only):  {master_path}", file=sys.stderr)
    print(f"[INFO] Output (write):      {output_path}", file=sys.stderr)

    # --- Load ---
    master = load_yaml(master_path)
    jd_text = load_text(args.jd)

    # --- Aggressive: filter to most JD-relevant roles before LLM call ---
    if args.level == "aggressive":
        exp = master["cv"]["sections"].get("experience", [])
        keep_companies = {"hancock", "modis"}
        filtered = [e for e in exp
                    if any(c in str(e.get("company", "")).lower()
                           for c in keep_companies)]
        master["cv"]["sections"]["experience"] = filtered
        print(f"[INFO] Aggressive: filtered experience to {len(filtered)} roles "
              f"({', '.join(e.get('company', '?') for e in filtered)})",
              file=sys.stderr)

    # Deep-copy for audit baseline (before LLM mutates anything)
    original = deepcopy(master)
    cv_text = load_text(master_path)  # full master text for audit/fabrication check

    # --- Pipeline ---
    system_prompt = build_system_prompt(args.level)
    content = asyncio.run(call_llm(
        system_prompt, build_user_prompt(cv_text, jd_text)))
    merged = merge_content(original, content)  # deepcopy — master untouched

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

    if not args.no_audit:
        hard, jd_adds = check_fabrication(original, merged, cv_text, jd_text)
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

    if not args.no_render:
        pdf = render_pdf(output_path)
        print(f"\nDone. {pdf}")
    else:
        print(f"\nDone. {output_path}")
        print("Run: uv run rendercv render " + str(output_path))


if __name__ == "__main__":
    main()
