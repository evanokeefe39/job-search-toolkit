"""YAML I/O, merge, and content-aware highlight trimming."""

import difflib
import sys
from copy import deepcopy
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_text(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _trim_highlights(original_highlights: list[str], new_highlights: list[str],
                     max_count: int = 5) -> list[str]:
    """Keep up to max_count new highlights with strongest overlap to originals.

    Uses difflib.SequenceMatcher to score each new bullet against every
    original bullet; sorts by best-match ratio descending. This prevents
    positional-trim shipping invented content — bullets with no master
    analogue score zero and drop first.
    """
    if len(new_highlights) <= max_count:
        return new_highlights
    scored: list[tuple[float, str]] = []
    for nh in new_highlights:
        best = max(
            (difflib.SequenceMatcher(None, nh, oh).ratio() for oh in original_highlights),
            default=0.0,
        )
        scored.append((best, nh))
    scored.sort(key=lambda x: x[0], reverse=True)
    kept = [hl for _, hl in scored[:max_count]]
    dropped_bullets = [hl for _, hl in scored[max_count:]]
    print(f"[TRIM] {len(dropped_bullets)}/{len(new_highlights)} bullets dropped "
          f"(content-aware, kept top {max_count})", file=sys.stderr)
    for i, db in enumerate(dropped_bullets):
        print(f"[TRIM]   dropped[{i}] (best overlap={scored[max_count + i][0]:.2f}): "
              f"{db[:120]}{'...' if len(db) > 120 else ''}", file=sys.stderr)
    return kept


def merge_content(original: dict, content: dict) -> dict:
    """Deep-copy original, then slot LLM content into the copy."""
    result = deepcopy(original)
    sections = result["cv"]["sections"]
    if "summary" in content:
        sections["summary"] = [content["summary"]]
    if "experiences" in content:
        orig_exp = sections.get("experience", [])
        for entry in content["experiences"]:
            idx = entry.get("index", 0)
            if idx < len(orig_exp) and "highlights" in entry:
                new_hl = entry["highlights"]
                orig_hl = orig_exp[idx].get("highlights", [])
                orig_exp[idx]["highlights"] = _trim_highlights(orig_hl, new_hl)
    if "skills" in content:
        sections["skills"] = content["skills"]
    return result


def emit_yaml(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    print(f"[INFO] Wrote: {path}", file=sys.stderr)


def validate_structure(data: dict, original: dict) -> None:
    sections = data.get("cv", {}).get("sections", {})
    oe = original.get("cv", {}).get("sections", {}).get("experience", [])
    ne = sections.get("experience", [])
    if len(ne) != len(oe):
        print(f"[WARN] Exp count: {len(oe)} -> {len(ne)}", file=sys.stderr)
