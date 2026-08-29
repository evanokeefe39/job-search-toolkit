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
import os
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
        base = _weighted_base(scores, w)
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

# ============================================================================
# Lead scoring (WS7 Epic 7.2) — score_engine as a second consumer
# ============================================================================
# Deterministic, zero-LLM: lead_score is a weighted sum over intent/fit/
# access/urgency, computed from warehouse signals (dim_company / dim_person /
# fact_touch / fact_referral). Weights live in versioned lead_scoring_config.
# yaml and change only via the gated lead-calibration path (never LLM).
# A MISSING signal contributes 'neutral' (0.5), never 0 — a thin contact is
# not penalized as disqualification.

LEAD_FEATURES = ("intent", "fit", "access", "urgency")
LEAD_MIN_EVIDENCE = 5


def _load_lead_config() -> dict:
    """Full lead-scoring config: weights + boost/neutral constants.

    Active override (env JST_LEAD_ACTIVE_WEIGHTS_FILE, default
    data/lead_scoring_active.yaml) overrides only the ``weights`` sub-dict;
    the boost/neutral constants stay from the bundled default. Mirrors
    score_jobs' versioned weight precedence (WS1).
    """
    import yaml

    from importlib import resources

    active = Path(os.environ.get(
        "JST_LEAD_ACTIVE_WEIGHTS_FILE", "data/lead_scoring_active.yaml"))
    if active.is_file():
        with open(active, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if cfg.get("weights"):
            bundled = _load_lead_config_bundled()
            bundled["weights"] = dict(cfg["weights"])
            return bundled
    return _load_lead_config_bundled()


def _load_lead_config_bundled() -> dict:
    import yaml

    from importlib import resources

    with resources.as_file(
        resources.files(__package__) / "lead_scoring_config.yaml"
    ) as p:
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)


def _load_lead_weights() -> dict[str, float]:
    """Active lead weights (override wins, else bundled defaults)."""
    return dict(_load_lead_config()["weights"])


def _load_default_lead_weights() -> dict[str, float]:
    """Bundled default lead weights (never affected by an override file)."""
    return dict(_load_lead_config_bundled()["weights"])


LEAD_WEIGHTS = _load_lead_weights()


def _weighted_base(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted sum of per-feature scores — shared by score_jobs and score_leads."""
    return sum(scores[k] * w for k, w in weights.items())


def validate_lead_weights(weights: dict[str, float]) -> None:
    """Fail loudly unless the weight vector covers every dimension and sums
    to 1.0 (never silently default on a corrupt/invalid config)."""
    missing = [f for f in LEAD_FEATURES if f not in weights]
    if missing:
        raise ValueError(f"lead weights missing dimensions: {missing}")
    total = sum(weights[f] for f in LEAD_FEATURES)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"lead weights must sum to 1.0, got {total}")


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _score_lead_intent(lead: dict, cfg: dict) -> float:
    """Intent: sector/role overlap; an inbound touch raises the baseline."""
    if lead.get("inbound_touch"):
        base = lead.get("sector_overlap")
        base = cfg["neutral"] if base is None else float(base)
        return _clamp(base + cfg["inbound_intent_boost"])
    v = lead.get("sector_overlap")
    return cfg["neutral"] if v is None else _clamp(float(v))


def _score_lead_fit(lead: dict, cfg: dict) -> float:
    v = lead.get("tech_overlap")
    return cfg["neutral"] if v is None else _clamp(float(v))


def _score_lead_access(lead: dict, cfg: dict) -> float:
    """Access: contact availability + referral boost + recent engagement."""
    ca = lead.get("contact_available")
    base = cfg["neutral"] if ca is None else (cfg["neutral"] if ca else 0.3)
    if lead.get("has_referral"):
        base += cfg["referral_boost"]
    rtd = lead.get("recent_touch_days")
    if rtd is not None and int(rtd) <= 30:
        base += 0.1
    return _clamp(base)


def _score_lead_urgency(lead: dict, cfg: dict) -> float:
    """Urgency: recent funding + active hiring + recent inbound/event."""
    funding = lead.get("funding_amount_usd")
    funding_score = (
        cfg["neutral"] if funding is None
        else _clamp(float(funding) / 100_000_000)
    )
    hiring = lead.get("active_hiring")
    hiring_score = cfg["neutral"] if hiring is None else (0.8 if hiring else 0.3)
    rtd = lead.get("recent_touch_days")
    engagement = cfg["neutral"] if rtd is None else (0.7 if int(rtd) <= 30 else 0.3)
    return _clamp(0.5 * funding_score + 0.3 * hiring_score + 0.2 * engagement)


def score_leads(leads: list[dict], weights: dict[str, float] | None = None) -> list[dict]:
    """Score all leads deterministically; add lead_scores + lead_score, rank
    by lead_score DESC. Missing signals score neutral (0.5), never a crash."""
    cfg = _load_lead_config()
    w = weights if weights is not None else LEAD_WEIGHTS
    validate_lead_weights(w)
    for lead in leads:
        lead_scores = {
            "intent": _score_lead_intent(lead, cfg),
            "fit": _score_lead_fit(lead, cfg),
            "access": _score_lead_access(lead, cfg),
            "urgency": _score_lead_urgency(lead, cfg),
        }
        lead["lead_scores"] = lead_scores
        lead["lead_score"] = round(_weighted_base(lead_scores, w), 3)
    leads.sort(key=lambda l: l.get("lead_score", 0), reverse=True)
    return leads


# --- warehouse write path (gold.lead_rank / gold.lead_score_calibration) ---

LEAD_COLUMNS: list[tuple[str, str]] = [
    ("person_id", "VARCHAR"),
    ("company_id", "VARCHAR"),
    ("intent", "DOUBLE"),
    ("fit", "DOUBLE"),
    ("access", "DOUBLE"),
    ("urgency", "DOUBLE"),
    ("lead_score", "DOUBLE"),
    ("scored_at", "TIMESTAMP"),
]


def ensure_lead_table(con) -> None:
    """Create ``silver.lead`` if missing (idempotent)."""
    from .silver import sql_literal  # noqa: F401  (kept for symmetry)

    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    cols = ", ".join(f"{n} {t}" for n, t in LEAD_COLUMNS)
    con.execute(f"CREATE TABLE IF NOT EXISTS silver.lead ({cols}, PRIMARY KEY (person_id))")


def upsert_lead_scores(con, leads: list[dict]) -> int:
    """Score leads deterministically and upsert into silver.lead (idempotent)."""
    from .silver import sql_literal

    ensure_lead_table(con)
    scored = score_leads(leads)
    updated = 0
    for lead in scored:
        dims = lead["lead_scores"]
        con.execute(
            "INSERT INTO silver.lead (person_id, company_id, intent, fit, access, "
            "urgency, lead_score, scored_at) VALUES "
            f"({sql_literal(lead.get('person_id'))}, "
            f"{sql_literal(lead.get('company_id'))}, "
            f"{sql_literal(dims['intent'])}, {sql_literal(dims['fit'])}, "
            f"{sql_literal(dims['access'])}, {sql_literal(dims['urgency'])}, "
            f"{sql_literal(lead['lead_score'])}, NOW()) "
            "ON CONFLICT (person_id) DO UPDATE SET "
            "company_id = EXCLUDED.company_id, intent = EXCLUDED.intent, "
            "fit = EXCLUDED.fit, access = EXCLUDED.access, "
            "urgency = EXCLUDED.urgency, lead_score = EXCLUDED.lead_score, "
            "scored_at = NOW()"
        )
        updated += 1
    return updated


def lead_apply_calibration(db_path) -> dict:
    """Gated lead-weight calibration (Epic 7.2).

    Mirrors WS1's job calibration: weights change only via this explicit
    gated path, promoted from SQL evidence in gold.lead_score_calibration.
    The versioned-config + active-override-file write machinery is reused;
    outcome-LINKED advance evidence is gated until lead outcomes exist, so
    with no scored leads it refuses ('not enough data') and writes nothing.
    """
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        total = con.execute(
            "SELECT COALESCE(SUM(lead_count), 0) FROM gold.lead_score_calibration"
        ).fetchone()[0]
    except duckdb.Error:
        total = 0
    finally:
        con.close()
    if int(total) < LEAD_MIN_EVIDENCE:
        raise RuntimeError("not enough data — no calibration")
    # Outcome-linked advance evidence gates the write; deferred until lead
    # outcome labels exist (per lead-scoring.md assumption log).
    raise RuntimeError("not enough data — no outcome evidence for calibration")
