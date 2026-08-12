"""Stage 5: Score jobs and produce ranked recommendations.

Scores each job across dimensions relevant to the user's goals:
- Well paid, not too demanding
- Flexibility to travel
- Work-life balance for side projects
- Interesting tech stack

Idempotent — re-scores every run (scoring is cheap, no LLM needed).
Reads from `freework_jobs_enriched.json`, writes scored output.

Usage (legacy CLI — prefer the Dagster asset):
    python -m job_search_toolkit.pipelines.jd.score_engine
    python -m job_search_toolkit.pipelines.jd.score_engine --top 20
    python -m job_search_toolkit.pipelines.jd.score_engine --export-csv ranked.csv
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ENRICHED_JOBS = Path("data/silver/freework_jobs_enriched.json")

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


def _get_pay(job: dict) -> tuple[float, float]:
    """Extract normalized annual EUR pay from canonical salary field."""
    salary = job.get("salary") or {}
    if salary.get("is_disclosed"):
        mn = salary.get("min_annual_eur")
        mx = salary.get("max_annual_eur")
        # Use whichever is available
        if mn is not None or mx is not None:
            return mn or mx or 0.0, mx or mn or 0.0
    return 0.0, 0.0


def _score_pay(job: dict) -> float:
    """Score pay: higher is better, normalized against market range."""
    pay_min, pay_max = _get_pay(job)
    if pay_min == 0:
        return 0.3  # unknown -> neutral
    avg = (pay_min + pay_max) / 2
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

    workplace = (job.get("workplace_type") or "").lower()
    if workplace == "remote":
        score += 0.3
    elif workplace == "hybrid":
        score += 0.15

    contracts = job.get("contract_types") or []
    if "contract" in contracts:
        score += 0.2  # contractor = more flexibility
    if "full_time" in contracts:
        score += 0.0  # CDI is neutral

    # Short duration = less commitment
    duration = (job.get("contract_duration") or "").lower()
    if any(d in duration for d in ["month", "mois"]):
        score += 0.1

    return min(score, 1.0)


def _score_low_responsibility(job: dict) -> float:
    """Score for low-to-moderate responsibility."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description_text") or "").lower()
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

    # Seniority signal (canonical: entry, junior, mid, senior, lead, manager)
    if seniority in ("lead", "manager"):
        score -= 0.2
    elif seniority == "senior":
        score -= 0.05
    elif seniority in ("entry", "junior"):
        score += 0.1

    # Role category signal
    if role == "product_manager":
        score -= 0.15
    if role == "data_analyst":
        score += 0.1

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
    """Score how well the tech stack matches modern data engineering."""
    technologies = job.get("technologies") or []

    all_tech = {t.lower() for t in technologies}
    if not all_tech:
        desc = (job.get("description_text") or "").lower()
        all_tech = set(desc.split())

    high_value_count = len(all_tech & HIGH_VALUE_TECH)
    legacy_count = len(all_tech & LEGACY_TECH)
    total_signals = max(high_value_count + legacy_count, 4)

    score = (high_value_count - legacy_count * 0.5) / total_signals
    return max(min(score + 0.5, 1.0), 0.0)


# --- Company engagement heuristic (non-LLM) ----------------------------------
# Replaces the LLM classify signal on the ranking path. This is a heuristic,
# not LLM-grade: it will misclassify some postings (e.g. an ESN whose name
# has no signal and whose description is English) and is expected to be tuned
# from data. Engagement from the source (hiringcafe ships "direct") wins when
# present; freework rows fall back to this detector until enriched.
ESN_NAME_SIGNALS = (
    "consulting", "conseil", "esn", "ssii", "recruitment", "staffing",
    "groupe", "holding",
)
ESN_DESC_SIGNALS = (
    "chez notre client", "mission chez", "en mission", "client final",
)


def detect_engagement(company_name: str, description: str) -> str:
    """Tabular ESN/direct detection from name + description patterns.

    Returns ``"consulting"`` when the company name or the posting text shows
    ESN/consulting signals, else ``"direct"``.
    """
    name = (company_name or "").lower()
    desc = (description or "").lower()
    if any(s in name for s in ESN_NAME_SIGNALS):
        return "consulting"
    if any(s in desc for s in ESN_DESC_SIGNALS):
        return "consulting"
    return "direct"


def _score_company_quality(job: dict) -> float:
    """Score company quality: product company > consulting, known name > obscure.

    Consumes only tabular fields: ``engagement_type`` when the source
    provides it (hiringcafe), otherwise the ``detect_engagement`` heuristic;
    ``org_type`` / ``stock_symbol`` / funding come from the ``dim_company``
    join (via ``company_info``). No LLM call on this path.
    """
    score = 0.5  # baseline

    posting_type = (job.get("posting_company_type") or "").lower()
    engagement = (job.get("engagement_type") or "").lower()
    ci = job.get("company_info") or {}

    if engagement not in ("direct", "consulting"):
        # Source didn't classify (freework without enrich) — use the heuristic.
        engagement = detect_engagement(
            ci.get("name") or job.get("company") or "",
            job.get("description_text") or "",
        )
    if engagement == "direct":
        score += 0.2
    if posting_type == "esn":
        score -= 0.05

    org_type = (ci.get("org_type") or "").lower()
    if org_type == "enterprise":
        score += 0.1
    if org_type == "consulting_firm":
        score -= 0.1

    # Well-known company = has stock symbol or significant funding
    if ci.get("stock_symbol") or (
        ci.get("latest_funding_amount_usd") and ci["latest_funding_amount_usd"] > 50_000_000
    ):
        score += 0.05

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
