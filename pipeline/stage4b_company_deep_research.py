"""Stage 4b: Deep company research — exhaustive profile for every posting company.

Idempotent — skips companies that already have `company_deep_research`.
Reads from and writes to `freework_jobs_enriched.json`.

Usage:
    python -m pipeline.stage4b_company_deep_research
    python -m pipeline.stage4b_company_deep_research --smoke 5
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

DEEP_RESEARCH_SYSTEM = """You are researching French IT consulting/recruitment companies for a senior data engineer job seeker in Paris.

Provide a structured profile of the company. Be honest about what you know vs. what you're inferring.

Return a JSON object:
{
  "company_name": "canonical name",
  "company_type": "esn" | "cabinet_recrutement" | "editeur_logiciel" | "startup" | "grand_compte" | "pure_player_data" | "other",
  "size_france": "string — e.g. '50-200 employees', 'unknown'",
  "founded": "string or null",
  "headquarters": "string or null",
  "specialization": "string — what do they specialize in? e.g. 'generalist ESN', 'data & AI consulting', 'finance sector only'",
  "client_types": ["grand_compte", "pme", "startup", "public_sector", ...],
  "reputation": "string — 1-2 sentences on reputation as an employer/contractor partner in France",
  "known_for": ["string — 1-3 things they're known for"],
  "payment_reliability": "string or null — anything known about paying contractors on time",
  "remote_policy": "string or null — known stance on remote work for contractors",
  "would_recommend": true | false | null — based on what you know, would you recommend this company to a data engineer seeking well-paid, flexible contract work in Paris?",
  "recommendation_notes": "string — brief reasoning for the recommendation",
  "info_quality": "high" | "medium" | "low" | "unknown"
}

ESN types:
- "esn" = Société de Services Numériques (formerly SSII) — general IT services/consulting
- "cabinet_recrutement" = recruitment agency / headhunter
- "editeur_logiciel" = software publisher / product company
- "pure_player_data" = data-specialist consultancy (not general IT)

Rules:
- For well-known French ESNs (Sopra Steria, Capgemini, Atos, etc.) provide detailed info.
- For smaller/niche firms, note what you can infer from their name and common patterns.
- If you truly don't know a company, set info_quality to "unknown" and be honest.
- Output ONLY the JSON object."""


def load_jobs(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_jobs(path: Path, jobs: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def _build_prompt(company: str, job_count: int, sectors: list[str], avg_rate: str) -> str:
    return (
        f"Company: {company}\n"
        f"Jobs posted on free-work.com: {job_count}\n"
        f"Sectors their clients are in: {', '.join(sectors) if sectors else 'unknown'}\n"
        f"Average daily rate offered: {avg_rate}\n"
        f"\nResearch this company as a potential employer/contracting partner for a senior data engineer in Paris."
    )


async def deep_research_companies(
    jobs: list[dict],
    smoke: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    llm = LLMClient()

    # Aggregate per company
    companies: dict[str, dict] = {}
    for j in jobs:
        co = j["company"]
        if co not in companies:
            companies[co] = {"count": 0, "sectors": set(), "rates": []}
        companies[co]["count"] += 1
        sec = j.get("end_client_sector")
        if sec and sec != "?":
            companies[co]["sectors"].add(sec)
        rate = j.get("rate", "")
        if rate:
            import re
            nums = re.findall(r"(\d+)", rate.replace("\xa0", "").replace("\u202f", ""))
            if nums:
                companies[co]["rates"].append(float(nums[-1]))

    # Check which already have deep research
    researched = set()
    for j in jobs:
        if j.get("company_deep_research"):
            researched.add(j["company"])

    to_research = [(co, d) for co, d in companies.items() if co not in researched]
    if smoke is not None:
        to_research = to_research[:smoke]

    total_jobs_affected = sum(d["count"] for _, d in to_research)

    est = estimate_cost(
        len(to_research),
        tokens_in_per_item=200,
        tokens_out_per_item=250,
        label="Stage 4b: Deep Company Research",
    )
    print_estimate(est)
    print(f"Companies to research: {len(to_research)} (affecting {total_jobs_affected} jobs)")

    if dry_run:
        for co, d in to_research[:20]:
            print(f"  {co} ({d['count']} jobs)")
        return jobs

    if not to_research:
        print("All companies already researched.")
        return jobs

    batch_size = 8
    for batch_start in range(0, len(to_research), batch_size):
        batch = to_research[batch_start : batch_start + batch_size]
        prompts = []
        for co, d in batch:
            avg = f"{sum(d['rates'])/len(d['rates']):.0f} EUR/day" if d["rates"] else "unknown"
            prompts.append(_build_prompt(co, d["count"], sorted(d["sectors"]), avg))

        results = await llm.batch_complete_json(
            prompts,
            system=DEEP_RESEARCH_SYSTEM,
            temperature=0.3,
            max_tokens=400,
        )

        for (co, _), result in zip(batch, results):
            for j in jobs:
                if j["company"] == co:
                    j["company_deep_research"] = result

        done = min(batch_start + batch_size, len(to_research))
        print(f"  Researched {done} / {len(to_research)}")

    await llm.close()
    return jobs


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Stage 4b: Deep company research")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke", type=int, default=None)
    parser.add_argument("--input", type=Path, default=ENRICHED_JOBS)
    parser.add_argument("--output", type=Path, default=ENRICHED_JOBS)
    args = parser.parse_args()

    jobs = load_jobs(args.input)
    if not jobs:
        print(f"No jobs found in {args.input}")
        sys.exit(1)

    enriched = await deep_research_companies(
        jobs, smoke=args.smoke, dry_run=args.dry_run
    )
    if not args.dry_run:
        save_jobs(args.output, enriched)
        print(f"Saved {len(enriched)} jobs to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
