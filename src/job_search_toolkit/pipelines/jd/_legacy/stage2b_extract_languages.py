"""Stage 2b: Extract language requirements from job descriptions.

Idempotent — skips jobs that already have `language_requirements`.
Reads from and writes to `freework_jobs_enriched.json`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from ..config import ENRICHED_JOBS
from ..resources.llm_client import LLMClient
from ..smoke_utils import estimate_cost, print_estimate

EXTRACT_LANG_SYSTEM = """You extract language requirements from job descriptions.
Analyze the description for any mention of language skills needed.

Return a JSON object:
{
  "languages": [
    {
      "language": "french" | "english" | "dutch" | "german" | "other",
      "level": "native" | "bilingual" | "fluent" | "c1" | "c2" | "b2" | "b1" | "professional" | "technical" | "basic" | "not_specified",
      "requirement_type": "mandatory" | "preferred" | "nice_to_have" | "not_specified",
      "evidence": "short quote from the description"
    }
  ],
  "work_language": "french" | "english" | "bilingual" | "not_specified",
  "summary": "one-line summary of language requirements"
}

Rules:
- A job in France with no language mentioned at all → assume French is required, mark as "not_specified"
- "bilingue" / "bilingual" without qualification → level="bilingual"
- "anglais courant" / "fluent English" → level="fluent"
- "anglais technique" / "technical English" → level="technical"
- "anglais obligatoire" / "English required" → requirement_type="mandatory"
- If both French and English are mentioned as required, mark work_language="bilingual"
- Output ONLY the JSON object."""


def load_jobs(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_jobs(path: Path, jobs: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


async def extract_languages(
    jobs: list[dict],
    limit: int | None = None,
    dry_run: bool = False,
    smoke: int | None = None,
) -> list[dict]:
    llm = LLMClient()

    to_process: list[int] = []
    for i, job in enumerate(jobs):
        if job.get("language_requirements") is not None:
            continue
        desc = job.get("description_en") or job.get("description") or ""
        if not desc.strip():
            continue
        to_process.append(i)

    if limit is not None:
        to_process = to_process[:limit]
    if smoke is not None:
        to_process = to_process[:smoke]
        print(f"SMOKE MODE: only processing {len(to_process)} jobs")

    est = estimate_cost(
        len(to_process),
        tokens_in_per_item=500,
        tokens_out_per_item=100,
        label="Stage 2b: Language Requirements",
    )
    print_estimate(est)
    print(f"Jobs to extract: {len(to_process)} / {len(jobs)}")

    if dry_run or not to_process:
        return jobs

    batch_size = 15
    for batch_start in range(0, len(to_process), batch_size):
        batch_indices = to_process[batch_start : batch_start + batch_size]
        prompts = []
        for idx in batch_indices:
            desc = (jobs[idx].get("description_en") or jobs[idx].get("description") or "")[:3000]
            prompts.append(f"Job description:\n{desc}")

        results = await llm.batch_complete_json(
            prompts,
            system=EXTRACT_LANG_SYSTEM,
            temperature=0.2,
            max_tokens=256,
        )

        for idx, result in zip(batch_indices, results):
            jobs[idx]["language_requirements"] = result

        print(f"  Processed {batch_start + len(batch_indices)} / {len(to_process)}")

    await llm.close()
    return jobs


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Stage 2b: Extract language requirements")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--smoke", type=int, default=None)
    parser.add_argument("--input", type=Path, default=ENRICHED_JOBS)
    parser.add_argument("--output", type=Path, default=ENRICHED_JOBS)
    args = parser.parse_args()

    jobs = load_jobs(args.input)
    if not jobs:
        print(f"No jobs found in {args.input}")
        sys.exit(1)

    enriched = await extract_languages(
        jobs, limit=args.limit, dry_run=args.dry_run, smoke=args.smoke
    )
    if not args.dry_run:
        save_jobs(args.output, enriched)
        print(f"Saved {len(enriched)} jobs to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
