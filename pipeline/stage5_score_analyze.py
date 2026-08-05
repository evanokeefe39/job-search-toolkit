"""Stage 5: Score jobs and produce ranked recommendations.

Scores each job across dimensions relevant to the user's goals:
- Well paid, not too demanding
- Flexibility to travel
- Work-life balance for side projects
- Interesting tech stack

Idempotent — re-scores every run (scoring is cheap, no LLM needed).
Reads from `freework_jobs_enriched.json`, writes scored output.

Usage:
    python -m pipeline.stage5_score_analyze
    python -m pipeline.stage5_score_analyze --top 20
    python -m pipeline.stage5_score_analyze --export-csv ranked.csv
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .config import ENRICHED_JOBS

# --- Scoring weights (sum to 1.0) ---
# Tuned for: well-paid, not too demanding, flexible, interesting
WEIGHTS = {
    "pay": 0.30,
    "flexibility": 0.25,
    "low_responsibility": 0.20,
    "tech_match": 0.15,
    "company_quality": 0.10,
}

# High-value technologies for a modern data engineer
HIGH_VALUE_TECH = {
    # Cloud/platform
    "aws", "gcp", "azure", "databricks", "snowflake", "dbt", "terraform",
    # Orchestration
    "apache airflow", "dagster", "prefect",
    # Processing
    "apache spark", "pyspark", "apache kafka", "apache flink",
    # Languages
    "python", "sql", "scala",
    # DevOps
    "docker", "kubernetes", "ci/cd", "gitlab", "github actions",
    # Modern data stack
    "bigquery", "redshift", "looker", "tableau",
    # Specialty
    "dataiku", "clickhouse", "elasticsearch",
}

# Legacy/low-value tech that suggests maintenance-heavy roles
LEGACY_TECH = {
    "talend", "informatica", "ssis", "ssrs", "msbi", "cobol", "sas",
    "oracle forms", "crystal reports", "qlikview",
}


def load_jobs(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_pay(job: dict) -> tuple[float, float]:
    """Extract normalized annual pay in EUR. Returns (min, max) or (0, 0).

    Handles: "40k-75k €", "450-650 €" (daily rate), "700-1k €".
    """
    pay_str = job.get("pay") or ""
    rate_str = job.get("rate") or ""

    def _parse_single(value: str) -> float:
        """Parse one figure: '40k' -> 40000, '1 260' -> 1260."""
        v = value.strip().replace("\xa0", "").replace("\u202f", "").replace(" ", "")
        if v.lower().endswith("k"):
            return float(v[:-1]) * 1000
        return float(v)

    if pay_str:
        # Split on dash, strip currency suffix
        parts = re.split(r"\s*[-–—]\s*", pay_str.replace("\xa0", " "))
        parts = [re.sub(r"\s*[€¤$].*", "", p).strip() for p in parts]
        nums = [_parse_single(p) for p in parts if p]
        if len(nums) >= 2:
            return nums[0], nums[-1]
        if len(nums) == 1:
            return nums[0], nums[0]

    # Fall back to daily rate x 220 days/year
    if rate_str:
        parts = re.split(r"\s*[-–—]\s*", rate_str.replace("\xa0", " "))
        parts = [re.sub(r"\s*[€¤$].*", "", p).strip() for p in parts]
        nums = [_parse_single(p) for p in parts if p]
        if len(nums) >= 2:
            return nums[0] * 220, nums[-1] * 220
        if len(nums) == 1:
            return nums[0] * 220, nums[0] * 220
    return 0.0, 0.0


def _score_pay(job: dict) -> float:
    """Score pay: higher is better, normalized against market range."""
    pay_min, pay_max = _parse_pay(job)
    if pay_min == 0:
        return 0.3  # unknown → neutral
    avg = (pay_min + pay_max) / 2
    # Paris DE market: ~40k (junior) to ~120k+ (senior/lead contractor)
    # Score 0-1 where 80k+ = excellent
    if avg >= 90000:
        return 1.0
    elif avg >= 75000:
        return 0.8
    elif avg >= 60000:
        return 0.6
    elif avg >= 50000:
        return 0.4
    elif avg >= 40000:
        return 0.2
    else:
        return 0.1


def _score_flexibility(job: dict) -> float:
    """Score flexibility: remote work, contract type, travel potential."""
    score = 0.5  # baseline

    remote = (job.get("remote_type") or "").lower()
    if remote == "remote":
        score += 0.3
    elif remote == "hybrid":
        score += 0.15

    contracts = [c.lower() for c in job.get("contract_types", [])]
    if "contractor" in contracts:
        score += 0.2  # contractor = more flexibility
    if "permanent" in contracts:
        score += 0.0  # CDI is neutral

    # Short duration = less commitment, more flexibility to move on
    duration = (job.get("duration") or "").lower()
    if any(d in duration for d in ["month", "mois"]):
        score += 0.1

    return min(score, 1.0)


def _score_low_responsibility(job: dict) -> float:
    """Score for low-to-moderate responsibility (user wants not too demanding).
    Higher score = less demanding role."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description_en") or job.get("description") or "").lower()
    seniority = (job.get("seniority_level") or "").lower()
    role = (job.get("role_category") or "").lower()

    score = 0.5  # baseline

    # Title signals
    high_responsibility = {
        "lead", "architect", "manager", "head", "director",
        "chef de projet", "tech lead", "principal",
    }
    low_responsibility = {
        "junior", "support", "analyst", "analytics", "consultant",
    }

    title_words = set(title.split())
    if title_words & high_responsibility:
        score -= 0.25
    if title_words & low_responsibility:
        score += 0.15

    # Seniority signal
    if seniority in ("lead", "architect", "manager"):
        score -= 0.2
    elif seniority == "senior":
        score -= 0.05
    elif seniority == "junior":
        score += 0.1

    # Role category signal
    if role == "data_product_manager":
        score -= 0.15  # usually higher pressure
    if role == "data_analyst":
        score += 0.1  # typically less operational pressure than DE

    # Management keywords in description
    mgmt_keywords = [
        "manage a team", "lead a team", "mentor", "line management",
        "gérer une équipe", "management d'équipe",
    ]
    if any(kw in desc for kw in mgmt_keywords):
        score -= 0.2

    # On-call / production pressure
    ops_pressure = ["on-call", "production incidents", "astreinte", "incident", "pager"]
    if any(kw in desc for kw in ops_pressure):
        score -= 0.1

    return max(score, 0.0)


def _score_tech_match(job: dict) -> float:
    """Score how well the tech stack matches modern, enjoyable data engineering."""
    techs = job.get("extracted_technologies") or []
    skills = job.get("skills") or []

    all_tech = {t.lower() for t in techs + skills}
    if not all_tech:
        # Fall back to description keyword scan
        desc = (job.get("description_en") or job.get("description") or "").lower()
        all_tech = set(desc.split())

    high_value_count = len(all_tech & HIGH_VALUE_TECH)
    legacy_count = len(all_tech & LEGACY_TECH)
    total_signals = max(high_value_count + legacy_count, 4)

    score = (high_value_count - legacy_count * 0.5) / total_signals
    return max(min(score + 0.5, 1.0), 0.0)


def _score_company_quality(job: dict) -> float:
    """Score company quality: product company > consulting, known name > obscure."""
    score = 0.5  # baseline

    posting_type = (job.get("posting_company_type") or "").lower()
    engagement = (job.get("engagement_type") or "").lower()
    stats = job.get("company_stats") or {}

    if engagement == "direct":
        score += 0.2  # direct hire more stable, better culture integration
    if posting_type == "esn":
        score -= 0.05  # consulting meat-grinder risk

    company_type = stats.get("company_type", "")
    if company_type == "enterprise":
        score += 0.1  # stability, benefits
    if company_type == "consulting_firm":
        score -= 0.1  # body-shop risk

    info_quality = stats.get("info_quality", "")
    if info_quality == "high":
        score += 0.05  # well-known company

    return max(min(score, 1.0), 0.0)


def score_jobs(jobs: list[dict]) -> list[dict]:
    """Score all jobs and add scores + ranking."""
    for job in jobs:
        scores = {
            "pay": _score_pay(job),
            "flexibility": _score_flexibility(job),
            "low_responsibility": _score_low_responsibility(job),
            "tech_match": _score_tech_match(job),
            "company_quality": _score_company_quality(job),
        }
        overall = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
        job["scores"] = scores
        job["overall_score"] = round(overall, 3)

    # Sort by overall score descending
    jobs.sort(key=lambda j: j.get("overall_score", 0), reverse=True)

    # Assign tiers
    for i, job in enumerate(jobs):
        score = job.get("overall_score", 0)
        if score >= 0.70:
            job["recommendation_tier"] = "top"
        elif score >= 0.60:
            job["recommendation_tier"] = "high"
        elif score >= 0.50:
            job["recommendation_tier"] = "medium"
        else:
            job["recommendation_tier"] = "low"

    return jobs


def export_csv(jobs: list[dict], path: Path) -> None:
    """Export scored jobs to CSV for spreadsheet analysis."""
    fieldnames = [
        "overall_score",
        "recommendation_tier",
        "scores_pay",
        "scores_flexibility",
        "scores_low_responsibility",
        "scores_tech_match",
        "scores_company_quality",
        "title",
        "company",
        "end_client_sector",
        "end_client_name",
        "engagement_type",
        "seniority_level",
        "role_category",
        "contract_types",
        "pay",
        "rate",
        "remote_type",
        "location",
        "duration",
        "url",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            scores = job.get("scores", {})
            row = {
                "overall_score": job.get("overall_score"),
                "recommendation_tier": job.get("recommendation_tier"),
                "scores_pay": scores.get("pay"),
                "scores_flexibility": scores.get("flexibility"),
                "scores_low_responsibility": scores.get("low_responsibility"),
                "scores_tech_match": scores.get("tech_match"),
                "scores_company_quality": scores.get("company_quality"),
                "title": job.get("title"),
                "company": job.get("company"),
                "end_client_sector": job.get("end_client_sector"),
                "end_client_name": job.get("end_client_name"),
                "engagement_type": job.get("engagement_type"),
                "seniority_level": job.get("seniority_level"),
                "role_category": job.get("role_category"),
                "contract_types": " | ".join(job.get("contract_types", [])),
                "pay": job.get("pay"),
                "rate": job.get("rate"),
                "remote_type": job.get("remote_type"),
                "location": job.get("location"),
                "duration": job.get("duration"),
                "url": job.get("url"),
            }
            writer.writerow(row)


def print_summary(jobs: list[dict], top_n: int = 15) -> None:
    """Print a ranked summary to stdout."""
    print(f"\n{'='*80}")
    print(f"JOB SCORING SUMMARY — {len(jobs)} jobs analyzed")
    print(f"{'='*80}")
    print(f"Weights: pay={WEIGHTS['pay']:.0%} flexibility={WEIGHTS['flexibility']:.0%} "
          f"low_resp={WEIGHTS['low_responsibility']:.0%} tech={WEIGHTS['tech_match']:.0%} "
          f"company={WEIGHTS['company_quality']:.0%}")
    print()

    # Tier distribution
    tiers: dict[str, int] = {}
    for j in jobs:
        t = j.get("recommendation_tier", "?")
        tiers[t] = tiers.get(t, 0) + 1
    print(f"Tiers: top={tiers.get('top',0)} high={tiers.get('high',0)} "
          f"medium={tiers.get('medium',0)} low={tiers.get('low',0)}")

    # Sector distribution
    sectors: dict[str, int] = {}
    for j in jobs:
        s = j.get("end_client_sector") or "unknown"
        sectors[s] = sectors.get(s, 0) + 1
    print(f"\nSectors: {dict(sorted(sectors.items(), key=lambda x: -x[1]))}")

    print(f"\n{'─'*80}")
    print(f"TOP {top_n} RECOMMENDATIONS")
    print(f"{'─'*80}")
    print(f"{'Score':>6} {'Tier':>6} {'Pay':>5} {'Flex':>5} {'LoResp':>6} "
          f"{'Tech':>5} {'Co':>5} | {'Title':<50} | {'Company':<25} | Sector")
    print(f"{'─'*80}")

    for job in jobs[:top_n]:
        s = job.get("scores", {})
        title = (job.get("title") or "")[:48]
        company = (job.get("company") or "")[:23]
        sector = (job.get("end_client_sector") or "?")[:20]
        print(
            f"{job.get('overall_score',0):6.3f} "
            f"{job.get('recommendation_tier','?'):>6} "
            f"{s.get('pay',0):.2f} "
            f"{s.get('flexibility',0):.2f} "
            f"{s.get('low_responsibility',0):.2f} "
            f"{s.get('tech_match',0):.2f} "
            f"{s.get('company_quality',0):.2f} "
            f"| {title:<50} | {company:<25} | {sector}"
        )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 5: Score jobs and produce ranked recommendations"
    )
    parser.add_argument("--top", type=int, default=20, help="Number of top jobs to show")
    parser.add_argument("--export-csv", type=Path, default=None, help="Export CSV path")
    parser.add_argument("--input", type=Path, default=ENRICHED_JOBS)
    parser.add_argument("--output", type=Path, default=ENRICHED_JOBS)
    args = parser.parse_args()

    jobs = load_jobs(args.input)
    if not jobs:
        print(f"No jobs found in {args.input}")
        sys.exit(1)

    # Quick pre-scoring using scraped data only (no LLM enrichment needed)
    # This lets us get useful output even before LLM stages run
    jobs = score_jobs(jobs)

    print_summary(jobs, top_n=args.top)

    if args.export_csv:
        export_csv(jobs, args.export_csv)
        print(f"\nExported CSV to {args.export_csv}")

    # Save scored data back
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"Saved scored jobs to {args.output}")


if __name__ == "__main__":
    main()
