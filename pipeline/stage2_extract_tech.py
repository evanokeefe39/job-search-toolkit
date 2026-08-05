"""Stage 2: Extract technologies, competencies, role category, and seniority.

Idempotent — skips jobs that already have `extracted_technologies`.
Reads from and writes to `freework_jobs_enriched.json`.

Usage:
    python -m pipeline.stage2_extract_tech
    python -m pipeline.stage2_extract_tech --dry-run
    python -m pipeline.stage2_extract_tech --limit 5
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

EXTRACT_SYSTEM = """You are a technical recruiter and data engineer analyzing job descriptions.
Extract structured information from the job description below.

Return a JSON object with these fields:
- "technologies": list of specific technologies, tools, platforms, and frameworks mentioned (e.g., "Apache Spark", "GCP", "Snowflake", "Terraform", "Kubernetes"). Include cloud platforms, databases, orchestration tools, CI/CD tools, data formats, and programming languages. Be specific — "AWS" not "cloud", "PySpark" not "Spark".
- "competencies": list of non-technical skills and domain knowledge required (e.g., "data modeling", "stakeholder management", "Agile methodology", "incident management", "data quality", "fraud detection"). Include business domains like "credit risk", "regulatory reporting", "customer analytics".
- "seniority_level": one of "junior", "intermediate", "senior", "lead", "architect", "manager"
- "role_category": one of "data_engineer", "data_analyst", "data_scientist", "ml_engineer", "data_platform_engineer", "analytics_engineer", "devops_data", "data_product_manager", "other"

Rules:
- Only include items explicitly mentioned or strongly implied by the description.
- Use standardized names (e.g., "Apache Airflow" not "Airflow").
- For seniority: look for years of experience required, title keywords (lead, senior, junior), and responsibility level described.
- If a field has no clear evidence, use an empty list or null.
- Output ONLY the JSON object, no markdown fences, no preamble."""


def load_jobs(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_jobs(path: Path, jobs: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def _build_prompt(job: dict) -> str:
    """Build extraction prompt from job data."""
    parts = [f"Title: {job.get('title', '')}"]
    desc = job.get("description_en") or job.get("description") or ""
    parts.append(f"Description: {desc[:4000]}")
    skills = job.get("skills", [])
    if skills:
        parts.append(f"Posted skills: {', '.join(skills)}")
    return "\n\n".join(parts)


async def extract_tech(
    jobs: list[dict],
    limit: int | None = None,
    dry_run: bool = False,
    smoke: int | None = None,
) -> list[dict]:
    """Extract tech/competencies for jobs missing the field."""
    llm = LLMClient()

    to_process: list[int] = []
    for i, job in enumerate(jobs):
        if job.get("extracted_technologies") is not None:
            continue  # already processed
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
        tokens_in_per_item=600,
        tokens_out_per_item=300,
        label="Stage 2: Extract Tech",
    )
    print_estimate(est)

    print(f"Jobs to extract: {len(to_process)} / {len(jobs)}")
    if dry_run:
        for idx in to_process[:5]:
            j = jobs[idx]
            print(f"  [{idx}] {j['title'][:80]}")
        return jobs

    if not to_process:
        print("Nothing to extract.")
        return jobs

    batch_size = 10
    for batch_start in range(0, len(to_process), batch_size):
        batch_indices = to_process[batch_start : batch_start + batch_size]
        prompts = [_build_prompt(jobs[idx]) for idx in batch_indices]

        results = await llm.batch_complete_json(
            prompts,
            system=EXTRACT_SYSTEM,
            temperature=0.2,
            max_tokens=1024,
        )

        for idx, result in zip(batch_indices, results):
            jobs[idx]["extracted_technologies"] = result.get("technologies", [])
            jobs[idx]["extracted_competencies"] = result.get("competencies", [])
            jobs[idx]["seniority_level"] = result.get("seniority_level")
            jobs[idx]["role_category"] = result.get("role_category")

        print(f"  Processed {batch_start + len(batch_indices)} / {len(to_process)}")

    await llm.close()
    return jobs


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 2: Extract technologies and competencies"
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

    enriched = await extract_tech(jobs, limit=args.limit, dry_run=args.dry_run, smoke=args.smoke)

    if not args.dry_run:
        save_jobs(args.output, enriched)
        print(f"Saved {len(enriched)} jobs to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
