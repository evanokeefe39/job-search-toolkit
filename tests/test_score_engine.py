"""Unit tests for the tabular scoring engine (score_engine).

Covers the deterministic, zero-LLM ranking dimensions: pay, flexibility,
low_responsibility, tech_match, and the multiplicative freshness factor.
company_quality was removed from the ranking (v2) — company reputation is a
post-shortlist enrichment, not a ranking dimension.
"""
from __future__ import annotations

import pytest

from datetime import date, timedelta

from job_search_toolkit.pipelines.jd.score_engine import (
    _score_freshness,
    _score_low_responsibility,
    _score_pay,
    score_jobs,
)


def test_pay_disclosed_high_scores_high():
    job = {"salary": {"is_disclosed": True, "min_annual_eur": 95000, "max_annual_eur": 105000}}
    assert _score_pay(job) == 1.0


def test_pay_disclosed_mid_beats_undisclosed():
    # Known mid-range salary ranks above a hidden one (coherent ordering).
    undisclosed = _score_pay({"salary": {}})
    disclosed_50k = _score_pay({"salary": {"is_disclosed": True, "min_annual_eur": 50000}})
    assert disclosed_50k > undisclosed
    assert disclosed_50k == 0.6


def test_pay_undisclosed_is_neutral_not_penalty():
    # Most boards don't disclose salary; that is NOT a low-pay signal.
    assert _score_pay({"salary": {}}) == 0.5
    assert _score_pay({}) == 0.5


def test_pay_known_low_below_neutral():
    # A known 35k salary is genuinely low -> ranks below unknown.
    assert _score_pay({"salary": {"is_disclosed": True, "min_annual_eur": 35000}}) == 0.4
    assert _score_pay({"salary": {"is_disclosed": True, "min_annual_eur": 35000}}) < 0.5


def test_low_responsibility_neutral_for_plain_role():
    job = {"title": "Data Engineer", "role_category": "", "seniority_level": "",
           "description_text": ""}
    assert _score_low_responsibility(job) == 0.5


def test_low_responsibility_penalizes_lead_titles():
    job = {"title": "Tech Lead", "role_category": "", "seniority_level": "",
           "description_text": ""}
    assert _score_low_responsibility(job) < 0.5


def test_low_responsibility_hyphenated_title_caught():
    # Compound/hyphenated titles previously slipped through the whitespace split.
    job = {"title": "Data-Lead", "role_category": "", "seniority_level": "",
           "description_text": ""}
    assert _score_low_responsibility(job) < 0.5


def test_low_responsibility_french_phrase_caught():
    job = {"title": "Chef de Projet", "role_category": "", "seniority_level": "",
           "description_text": ""}
    assert _score_low_responsibility(job) < 0.5


def test_low_responsibility_analyst_boosted():
    job = {"title": "Data Analyst", "role_category": "data_analyst",
           "seniority_level": "", "description_text": ""}
    assert _score_low_responsibility(job) > 0.5


def test_freshness_penalizes_old_and_stale():
    today = date(2026, 8, 24)
    fresh = {"date_posted": "2026-08-20", "last_seen_at": "2026-08-24"}
    stale = {"date_posted": "2026-01-01", "last_seen_at": "2026-01-01"}
    assert _score_freshness(fresh, today) > 0.9
    assert _score_freshness(stale, today) == 0.3  # floor, not zeroed
    assert _score_freshness(fresh, today) > _score_freshness(stale, today)


def test_freshness_missing_dates_is_neutral():
    assert _score_freshness({}, today=date(2026, 8, 24)) == 0.5


def test_score_jobs_applies_freshness_multiplier():
    base = {
        "title": "Data Engineer",
        "description_text": "Build data platform with Spark",
        "company": "Acme",
        "technologies": ["spark", "python", "sql"],
        "salary": {"min_annual_eur": 80000.0, "max_annual_eur": 100000.0,
                   "is_disclosed": True},
        "workplace_type": "remote",
        "contract_types": ["contract"],
        "seniority_level": "senior",
        "role_category": "data_engineer",
        "engagement_type": "direct",
        "posting_company_type": "end_client",
        "company_info": {},
    }
    fresh = dict(base, date_posted="2026-08-20", last_seen_at="2026-08-24")
    stale = dict(base, date_posted="2026-01-01", last_seen_at="2026-01-01")
    fresh_job, stale_job = score_jobs([stale, fresh])
    assert fresh_job["overall_score"] > stale_job["overall_score"]
    assert fresh_job["scores"]["freshness"] > stale_job["scores"]["freshness"]


def test_score_jobs_has_no_company_quality():
    job = {
        "title": "Data Engineer", "description_text": "build", "company": "Acme",
        "technologies": ["python", "sql"], "salary": {},
        "workplace_type": "remote", "contract_types": ["full_time"],
        "seniority_level": "", "role_category": "", "engagement_type": "direct",
        "company_info": {"org_type": "enterprise"},
        "date_posted": (date.today() - timedelta(days=2)).isoformat(),
        "last_seen_at": date.today().isoformat(),
    }
    out = score_jobs([job])[0]
    assert "company_quality" not in out["scores"]


def _bit_for_bit_fixture():
    """Fixed 10-job fixture spanning all scoring dimensions.

    Dates are computed relative to ``date.today()`` so the freshness
    component (which uses ``date.today()`` internally) is stable across
    runs: the test pins relative ages, not absolute dates.
    """
    today = date.today()
    return [
        {   # high pay, remote, modern stack, fresh
            "job_id": "J01",
            "company": "Vibe.co",
            "title": "Data Engineer",
            "description_text": "We build CTV advertising platforms with dbt and Snowflake.",
            "salary": {"is_disclosed": True, "min_annual_eur": 85000, "max_annual_eur": 105000},
            "workplace_type": "remote",
            "contract_types": ["full_time"],
            "technologies": ["python", "dbt", "snowflake", "airflow", "docker"],
            "engagement_type": "direct",
            "company_info": {"name": "Vibe.co", "org_type": "enterprise", "stock_symbol": "VIBE"},
            "date_posted": (today - timedelta(days=2)).isoformat(),
            "last_seen_at": today.isoformat(),
        },
        {   # consulting by name, low pay, legacy stack, old post
            "job_id": "J02",
            "company": "Finax Consulting",
            "title": "Consultant Data",
            "description_text": "En mission chez notre client, maintenance COBOL et mainframe.",
            "salary": {"is_disclosed": True, "min_annual_eur": 38000, "max_annual_eur": 42000},
            "workplace_type": "onsite",
            "contract_types": ["contract"],
            "contract_duration": "6 mois",
            "technologies": ["cobol", "siebel", "sharepoint", "oracle forms"],
            "company_info": {"name": "Finax Consulting"},
            "date_posted": (today - timedelta(days=85)).isoformat(),
            "last_seen_at": (today - timedelta(days=80)).isoformat(),
        },
        {   # hybrid, mid pay, junior, analyst role
            "job_id": "J03",
            "company": "UpClear",
            "title": "Junior Analytics Engineer",
            "description_text": "Power BI developer for BluePlanner, sql and tableau.",
            "salary": {"is_disclosed": True, "min_annual_eur": 52000, "max_annual_eur": 60000},
            "workplace_type": "hybrid",
            "contract_types": ["full_time"],
            "seniority_level": "junior",
            "role_category": "data_analyst",
            "technologies": ["sql", "tableau", "python"],
            "date_posted": (today - timedelta(days=10)).isoformat(),
            "last_seen_at": (today - timedelta(days=3)).isoformat(),
        },
        {   # lead role with team management, no salary
            "job_id": "J04",
            "company": "DataCorp",
            "title": "Tech Lead Data Platform",
            "description_text": "Lead a team of engineers, manage a team, on-call rotation.",
            "salary": {},
            "workplace_type": "onsite",
            "contract_types": ["full_time"],
            "seniority_level": "lead",
            "technologies": ["aws", "terraform", "kubernetes", "spark", "kafka", "gcp", "azure", "scala"],
            "company_info": {"name": "DataCorp", "org_type": "enterprise", "latest_funding_amount_usd": 120_000_000},
            "date_posted": (today - timedelta(days=1)).isoformat(),
            "last_seen_at": today.isoformat(),
        },
        {   # direct enterprise, good pay, fresh
            "job_id": "J05",
            "company": "Shopwave",
            "title": "Senior Data Engineer",
            "description_text": "Build pipelines with dagster and bigquery.",
            "salary": {"is_disclosed": True, "min_annual_eur": 90000, "max_annual_eur": 100000},
            "workplace_type": "remote",
            "contract_types": ["full_time"],
            "seniority_level": "senior",
            "engagement_type": "direct",
            "company_info": {"name": "Shopwave", "org_type": "enterprise"},
            "date_posted": (today - timedelta(days=5)).isoformat(),
            "last_seen_at": today.isoformat(),
        },
        {   # contract mission
            "job_id": "J06",
            "company": "Kappa Conseil",
            "title": "Data Engineer",
            "description_text": "Mission chez un client final du secteur bancaire.",
            "salary": {"is_disclosed": True, "min_annual_eur": 60000, "max_annual_eur": 65000},
            "workplace_type": "hybrid",
            "contract_types": ["contract"],
            "contract_duration": "12 mois",
            "technologies": ["python", "sql", "dbt"],
            "company_info": {"name": "Kappa Conseil", "org_type": "consulting_firm"},
            "date_posted": (today - timedelta(days=30)).isoformat(),
            "last_seen_at": (today - timedelta(days=10)).isoformat(),
        },
        {   # missing dates entirely, legacy tech only
            "job_id": "J07",
            "company": "OldLine Systems",
            "title": "Data Analyst",
            "description_text": "Support and reporting with legacy tools.",
            "salary": {"is_disclosed": True, "min_annual_eur": 45000, "max_annual_eur": 48000},
            "technologies": ["informatica", "siebel", "sharepoint", "crystal reports"],
        },
        {   # high pay onsite full_time, medium age
            "job_id": "J08",
            "company": "Fluxio",
            "title": "Platform Engineer",
            "description_text": "Streaming platform with kafka and flink, docker and gitlab.",
            "salary": {"is_disclosed": True, "min_annual_eur": 78000, "max_annual_eur": 88000},
            "workplace_type": "onsite",
            "contract_types": ["full_time"],
            "technologies": ["kafka", "flink", "docker", "gitlab"],
            "date_posted": (today - timedelta(days=45)).isoformat(),
            "last_seen_at": (today - timedelta(days=45)).isoformat(),
        },
        {   # short contractor mission, fresh, no salary
            "job_id": "J09",
            "company": "ByteBand",
            "title": "Analytics Engineer",
            "description_text": "dbt models and looker dashboards for the growth team.",
            "workplace_type": "remote",
            "contract_types": ["contract"],
            "contract_duration": "3 mois",
            "technologies": ["dbt", "looker", "bigquery"],
            "date_posted": today.isoformat(),
            "last_seen_at": today.isoformat(),
        },
        {   # product manager role, mid pay, slightly stale
            "job_id": "J10",
            "company": "Metricly",
            "title": "Lead Product Manager",
            "description_text": "Drive roadmap and mentor analysts.",
            "salary": {"is_disclosed": True, "min_annual_eur": 75000, "max_annual_eur": 85000},
            "workplace_type": "hybrid",
            "contract_types": ["full_time"],
            "seniority_level": "lead",
            "role_category": "product_manager",
            "technologies": ["sql", "tableau"],
            "date_posted": (today - timedelta(days=20)).isoformat(),
            "last_seen_at": (today - timedelta(days=20)).isoformat(),
        },
    ]


# Ground truth frozen from the CURRENT score_jobs() implementation (v2:
# company_quality removed, pay floor re-anchored, weights renormalized).
# Contract: any change to weights, decay curves, or tier thresholds that
# shifts any score or ranking here MUST fail this test.
_FROZEN = {
    "J01": (0.824, "top", {"pay": 1.0, "flexibility": 0.8, "low_responsibility": 0.5,
                           "tech_match": 1.0, "freshness": 0.989}),
    "J05": (0.759, "top", {"pay": 1.0, "flexibility": 0.8, "low_responsibility": 0.45,
                           "tech_match": 0.75, "freshness": 0.972}),
    "J09": (0.756, "top", {"pay": 0.5, "flexibility": 1.0, "low_responsibility": 0.65,
                           "tech_match": 1.0, "freshness": 1.0}),
    "J03": (0.677, "high", {"pay": 0.6, "flexibility": 0.65, "low_responsibility": 0.85,
                            "tech_match": 1.0, "freshness": 0.919}),
    "J06": (0.556, "medium", {"pay": 0.6, "flexibility": 0.9500000000000001, "low_responsibility": 0.5,
                              "tech_match": 1.0, "freshness": 0.75}),
    "J04": (0.47, "low", {"pay": 0.5, "flexibility": 0.5, "low_responsibility": 0.0,
                          "tech_match": 1.0, "freshness": 0.994}),
    "J10": (0.443, "low", {"pay": 0.8, "flexibility": 0.65, "low_responsibility": 0.0,
                           "tech_match": 1.0, "freshness": 0.722}),
    "J07": (0.262, "low", {"pay": 0.5, "flexibility": 0.5, "low_responsibility": 0.8,
                           "tech_match": 0.25, "freshness": 0.5}),
    "J08": (0.256, "low", {"pay": 0.8, "flexibility": 0.5, "low_responsibility": 0.5,
                           "tech_match": 1.0, "freshness": 0.375}),
    "J02": (0.172, "low", {"pay": 0.5, "flexibility": 0.7999999999999999, "low_responsibility": 0.65,
                           "tech_match": 0.25, "freshness": 0.3}),
}


def test_job_score_bit_for_bit():
    """Freeze current score_jobs() output bit-for-bit (contract)."""
    scored = {j["job_id"]: j for j in score_jobs(_bit_for_bit_fixture())}
    for job_id, (expected_score, expected_tier, expected_scores) in _FROZEN.items():
        job = scored[job_id]
        assert job["overall_score"] == expected_score, f"{job_id} overall"
        assert job["recommendation_tier"] == expected_tier, f"{job_id} tier"
        # approx: float representation (e.g. 0.9500000000000001) must not
        # break the contract — but the values are pinned tightly.
        assert job["scores"] == pytest.approx(expected_scores), f"{job_id} scores"