"""Stage 3: Classify company vertical, end-client sector, and engagement type.

CRITICAL: Most jobs are posted by consulting/ESN firms, not the end client.
This stage reads the DESCRIPTION to extract the end client's sector.

Idempotent — skips jobs that already have `end_client_sector`.
Reads from and writes to `freework_jobs_enriched.json`.

Usage:
    python -m pipeline.stage3_classify_vertical
    python -m pipeline.stage3_classify_vertical --dry-run
    python -m pipeline.stage3_classify_vertical --limit 5
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .config import ENRICHED_JOBS
from .llm_client import LLMClient
from .smoke_utils import estimate_cost, print_estimate

CLASSIFY_SYSTEM = """You are analyzing French IT job descriptions to classify the industry of the END CLIENT (not the consulting firm that posted the ad).

The posting company is often a consulting/ESN firm (Digital Services Company). The actual client is described in the job text. Extract:

Return a JSON object:
{
  "posting_company_type": "esn" | "end_client" | "startup" | "unknown",
  "end_client_name": "string or null — the name of the actual company this role is for, if explicitly stated",
  "end_client_sector": "string or null — the industry sector of the end client",
  "end_client_sector_confidence": "high" | "medium" | "low",
  "engagement_type": "direct" | "consulting" | "unknown"
}

Sector taxonomy (pick the closest match):
- "banking" (retail banking, investment banking, BFI)
- "insurance" (assurance, mutuelle)
- "asset_management" (gestion d'actifs, hedge funds)
- "fintech"
- "telecom" (télécommunications)
- "energy" (énergie, utilities, oil & gas)
- "healthcare" (santé, pharma, medtech)
- "luxury" (luxe, LVMH/Kering/etc.)
- "retail" (distribution, e-commerce)
- "aerospace_defense"
- "automotive"
- "railway_transport" (ferroviaire, transport)
- "media_entertainment"
- "public_sector" (gouvernement, administration)
- "real_estate"
- "manufacturing" (industrie)
- "technology" (pure tech/software company)
- "consulting" (the end client IS a consulting firm)
- "other"

Rules:
- Read the description carefully. French consulting ads often say "notre client, un acteur majeur du secteur X" or mention the client's industry.
- If the sector is ambiguous, use "low" confidence and your best guess.
- If the posting is directly by the end client (not an ESN), set engagement_type to "direct".
- Output ONLY the JSON object."""


def load_jobs(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_jobs(path: Path, jobs: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def _build_prompt(job: dict) -> str:
    parts = [
        f"Posting Company: {job.get('company', '')}",
        f"Job Title: {job.get('title', '')}",
    ]
    desc = job.get("description_en") or job.get("description") or ""
    parts.append(f"Description: {desc[:4000]}")
    return "\n\n".join(parts)

async def classify_verticals(
    jobs: list[dict],
    limit: int | None = None,
    dry_run: bool = False,
    smoke: int | None = None,
) -> list[dict]:
    llm = LLMClient()

    to_process: list[int] = []
    for i, job in enumerate(jobs):
        if job.get("end_client_sector") is not None:
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
        tokens_out_per_item=150,
        label="Stage 3: Classify Verticals",
    )
    print_estimate(est)

    print(f"Jobs to classify: {len(to_process)} / {len(jobs)}")
    if dry_run:
        for idx in to_process[:5]:
            j = jobs[idx]
            print(f"  [{idx}] {j['company']} — {j['title'][:70]}")
        return jobs

    if not to_process:
        print("Nothing to classify.")
        return jobs

    batch_size = 10
    for batch_start in range(0, len(to_process), batch_size):
        batch_indices = to_process[batch_start : batch_start + batch_size]
        prompts = [_build_prompt(jobs[idx]) for idx in batch_indices]

        results = await llm.batch_complete_json(
            prompts,
            system=CLASSIFY_SYSTEM,
            temperature=0.2,
            max_tokens=512,
        )

        for idx, result in zip(batch_indices, results):
            jobs[idx]["posting_company_type"] = result.get("posting_company_type")
            jobs[idx]["end_client_name"] = result.get("end_client_name")
            jobs[idx]["end_client_sector"] = result.get("end_client_sector")
            jobs[idx]["end_client_sector_confidence"] = result.get(
                "end_client_sector_confidence"
            )
            jobs[idx]["engagement_type"] = result.get("engagement_type")

        print(f"  Classified {batch_start + len(batch_indices)} / {len(to_process)}")

    await llm.close()
    return jobs


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 3: Classify company verticals and end-client sectors"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--smoke", type=int, default=None,
                        help="Smoke test: process only N jobs")
    parser.add_argument("--input", type=Path, default=ENRICHED_JOBS)
    parser.add_argument("--output", type=Path, default=ENRICHED_JOBS)
    args = parser.parse_args()

    jobs = load_jobs(args.input)
    if not jobs:
        print(f"No jobs found in {args.input}")
        sys.exit(1)

    enriched = await classify_verticals(
        jobs, limit=args.limit, dry_run=args.dry_run, smoke=args.smoke
    )

    if not args.dry_run:
        save_jobs(args.output, enriched)
        print(f"Saved {len(enriched)} jobs to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
