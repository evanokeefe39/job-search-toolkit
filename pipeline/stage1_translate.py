"""Stage 1: Translate French job descriptions to English.

Idempotent — skips jobs that already have a `description_en` field.
Reads `freework_jobs.json`, writes `freework_jobs_enriched.json`.

Usage:
    python -m pipeline.stage1_translate          # process all
    python -m pipeline.stage1_translate --dry-run # show what would be processed
    python -m pipeline.stage1_translate --limit 5 # process only 5
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .config import RAW_JOBS, ENRICHED_JOBS
from .llm_client import LLMClient
from .smoke_utils import estimate_cost, print_estimate

TRANSLATE_SYSTEM = """You are a technical translator specializing in IT job descriptions.
Translate the following French job description to English.

Rules:
- Preserve ALL technical terms, acronyms, and tool names exactly as-is (e.g., GCP, DBT, PySpark, CI/CD, Dataiku).
- Preserve salary/rate figures and currency symbols exactly.
- Keep the original structure and formatting cues (bullet points, sections).
- Translate idiomatic French business phrases naturally to English equivalents.
- If the description is already in English or mixed, translate only the French parts.
- Output ONLY the English translation, no preamble or explanation."""


def load_jobs(path: Path) -> list[dict]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_jobs(path: Path, jobs: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


async def translate_jobs(
    jobs: list[dict],
    limit: int | None = None,
    dry_run: bool = False,
    smoke: int | None = None,
) -> list[dict]:
    """Translate descriptions for jobs missing `description_en`."""
    llm = LLMClient()

    # Find jobs needing translation
    to_translate: list[int] = []
    for i, job in enumerate(jobs):
        desc = (job.get("description") or "").strip()
        if not desc:
            continue
        if job.get("description_en"):
            continue  # already translated
        # Quick heuristic: skip if already mostly English
        if _is_already_english(desc):
            jobs[i]["description_en"] = desc
            jobs[i]["_translation_source"] = "heuristic-already-english"
            continue
        to_translate.append(i)

    if limit is not None:
        to_translate = to_translate[:limit]

    if smoke is not None:
        to_translate = to_translate[:smoke]
        print(f"SMOKE MODE: only processing {len(to_translate)} jobs")

    est = estimate_cost(
        len(to_translate),
        tokens_in_per_item=800,   # ~800 tokens per description (avg French desc)
        tokens_out_per_item=600,  # ~600 tokens per English translation
        label="Stage 1: Translate",
    )
    print_estimate(est)

    print(f"Jobs to translate: {len(to_translate)} / {len(jobs)}")
    if dry_run:
        for idx in to_translate[:5]:
            j = jobs[idx]
            print(f"  [{idx}] {j['title'][:80]} — {j['company']}")
        return jobs

    if not to_translate:
        print("Nothing to translate.")
        return jobs

    # Translate in batches
    batch_size = 10
    for batch_start in range(0, len(to_translate), batch_size):
        batch_indices = to_translate[batch_start : batch_start + batch_size]
        prompts: list[str] = []
        for idx in batch_indices:
            desc = (jobs[idx].get("description") or "").strip()
            # Truncate very long descriptions to save tokens
            prompts.append(desc[:4000])

        translations = await llm.batch_complete(
            prompts,
            system=TRANSLATE_SYSTEM,
            temperature=0.2,
            max_tokens=2048,
        )

        for idx, translation in zip(batch_indices, translations):
            jobs[idx]["description_en"] = translation.strip()
            jobs[idx]["_translation_source"] = "deepseek-chat"

        print(
            f"  Translated {batch_start + len(batch_indices)} / {len(to_translate)}"
        )

    await llm.close()
    return jobs


def _is_already_english(text: str) -> bool:
    """Heuristic: check if text is already mostly English.
    French technical job descriptions contain many English loanwords (data, Python,
    pipeline) that dilute French signal, so use a very low threshold — any French
    function word suggests it needs translation.
    """
    common_fr = {
        "dans", "pour", "avec", "vous", "nous", "une", "est", "sur", "pas",
        "des", "les", "que", "qui", "par", "plus", "cette", "votre", "nos",
        "aux", "leurs", "d'une", "d'un", "l'équipe", "sera", "sont",
        "le", "la", "un", "du", "en", "ce", "au", "ou", "et", "de", "ne",
        "se", "à", "être", "tout", "aussi", "comme", "entre", "sous", "leur",
        "mais", "donc", "faire", "très", "bien", "avoir",
    }
    words = set(text.lower().split())
    if not words:
        return True
    fr_count = len(words & common_fr)
    # Any French function word at all → assume it needs translation.
    # False positives (sending English text to LLM) are cheap; false negatives
    # (skipping French text) are waste.
    return fr_count == 0


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Stage 1: Translate job descriptions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--smoke", type=int, default=None,
                        help="Smoke test: process only N jobs")
    parser.add_argument(
        "--input", type=Path, default=RAW_JOBS, help="Input JSON file"
    )
    parser.add_argument(
        "--output", type=Path, default=ENRICHED_JOBS, help="Output JSON file"
    )
    args = parser.parse_args()

    jobs = load_jobs(args.input)
    if not jobs:
        print(f"No jobs found in {args.input}")
        sys.exit(1)

    enriched = await translate_jobs(jobs, limit=args.limit, dry_run=args.dry_run, smoke=args.smoke)
    if not args.dry_run:
        save_jobs(args.output, enriched)
        print(f"Saved {len(enriched)} jobs to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
