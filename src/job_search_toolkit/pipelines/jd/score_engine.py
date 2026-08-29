"""Stage 5: Score jobs and produce ranked recommendations.

Scores each job across dimensions relevant to the user's goals:
- Well paid, not too demanding
- Flexibility to travel
- Work-life balance for side projects
- Interesting tech stack
- Freshness: penalize old posts and stale last-seen (decay factor applied
  multiplicatively so the tuned quality weights are untouched)

Idempotent — re-scores every run (scoring is cheap, no LLM needed).
Reads from `freework_jobs_enriched.json`, writes scored output.

Usage (legacy CLI — prefer the Dagster asset):
    python -m job_search_toolkit.pipelines.jd.score_engine
    python -m job_search_toolkit.pipelines.jd.score_engine --top 20
    python -m job_search_toolkit.pipelines.jd.score_engine --export-csv ranked.csv
"""

from __future__ import annotations

import re
from datetime import date, datetime
from importlib import resources
from pathlib import Path

from dotenv import load_dotenv

from .silver import STALE_AFTER_DAYS

load_dotenv()

ENRICHED_JOBS = Path("data/silver/freework_jobs_enriched.json")

# --- Scoring weights (sum to 1.0) ---
# Bundled defaults live in scoring_config.yaml (version 1). If the user-level
# active-override file written by `pipeline score-report --apply-calibration`
# exists (env JST_ACTIVE_WEIGHTS_FILE, default data/scoring_active.yaml), its
# weights take precedence; the packaged default stays intact and restorable.
def _load_default_weights() -> dict[str, float]:
    """Load active override weights if present, else the bundled defaults."""
    import yaml

    from .calibration import active_weights_file

    active = active_weights_file()
    if active.is_file():
        with open(active, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if config and config.get("weights"):
            return dict(config["weights"])
    with resources.as_file(resources.files(__package__) / "scoring_config.yaml") as p:
        with open(p, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    return dict(config["weights"])


WEIGHTS = _load_default_weights()

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


# Freshness decay curves (days). Age = since date_posted; seen = since
# last_seen_at. Fresh (0-7d) scores 1.0 and decays linearly to 0.
_FRESH_DAYS = 7
_MAX_AGE_DAYS = 90  # a post older than this is likely filled
# seen-decay horizon == STALE_AFTER_DAYS (silver.py); a job not seen for this
# long is treated as gone by the gold views too.


def _coerce_date(value) -> date | None:
    """Coerce a stored date/timestamp (str, date, or datetime) to a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _score_freshness(job: dict, today: date | None = None) -> float:
    """Score posting freshness: penalize old posts and stale last-seen.

    Combines two decay curves in [0, 1] (missing input -> neutral 0.5):
    - age: days since ``date_posted`` (fresh -> 1.0, linear to 0 at
      ``_MAX_AGE_DAYS``).
    - seen: days since ``last_seen_at`` (fresh -> 1.0, linear to 0 at
      ``STALE_AFTER_DAYS``).

    A floor of 0.3 keeps long-listed jobs ranked below fresh ones instead of
    zeroing them out. Applied multiplicatively to the weighted quality sum so
    the five tuned weights are untouched.
    """
    today = today or date.today()
    posted = _coerce_date(job.get("date_posted"))
    seen = _coerce_date(job.get("last_seen_at"))
    age = (
        0.5
        if posted is None
        else max(0.0, 1.0 - max((today - posted).days, 0) / _MAX_AGE_DAYS)
    )
    seen_score = (
        0.5
        if seen is None
        else max(0.0, 1.0 - max((today - seen).days, 0) / STALE_AFTER_DAYS)
    )
    return round(max(0.3, 0.5 * age + 0.5 * seen_score), 3)


def score_jobs(jobs: list[dict], weights: dict[str, float] | None = None) -> list[dict]:
    """Score all jobs and add scores + ranking.

    weights: optional per-dimension override; None uses the versioned
    scoring_config.yaml defaults (module constant WEIGHTS).
    """
    w = weights if weights is not None else WEIGHTS
    for job in jobs:
        scores = {
            "pay": _score_pay(job),
            "flexibility": _score_flexibility(job),
            "low_responsibility": _score_low_responsibility(job),
            "tech_match": _score_tech_match(job),
            "company_quality": _score_company_quality(job),
            "freshness": _score_freshness(job),
        }
        base = sum(scores[k] * w[k] for k in w)
        overall = base * scores["freshness"]
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
