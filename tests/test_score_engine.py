"""Unit tests for the tabular company engagement heuristic (score_engine).

The engagement detector replaces the LLM classify signal on the ranking
path. These tests pin the documented patterns; the heuristic is expected to
be tuned from data (see score_engine.py).
"""

from __future__ import annotations

from datetime import date, timedelta

from job_search_toolkit.pipelines.jd.score_engine import (
    detect_engagement,
    _score_company_quality,
    _score_freshness,
    score_jobs,
)


def test_detect_engagement_esn_name_consulting():
    assert detect_engagement("Finax Consulting", "Mission data platform") == "consulting"
    assert detect_engagement("SOFTEAM", "SSII - mission chez un grand compte") == "consulting"
    assert detect_engagement("emagine Consulting SARL", "text") == "consulting"
    assert detect_engagement("Groupe XYZ", "text") == "consulting"


def test_detect_engagement_esn_description_consulting():
    assert detect_engagement("Acme SAS", "En mission chez notre client final") == "consulting"
    assert detect_engagement("Acme SAS", "Intervention chez notre client") == "consulting"


def test_detect_engagement_direct_when_no_signal():
    assert detect_engagement("Vibe.co", "We build CTV advertising platforms") == "direct"
    assert detect_engagement("UpClear", "Power BI developer for BluePlanner") == "direct"


def test_detect_engagement_case_insensitive():
    assert detect_engagement("FINAX CONSULTING", "MISSION") == "consulting"
    assert detect_engagement("acme", "Chez Notre Client") == "consulting"


def test_detect_engagement_handles_none():
    assert detect_engagement(None, None) == "direct"
    assert detect_engagement("", "") == "direct"


def test_company_quality_uses_heuristic_when_engagement_unknown():
    """Freework rows without LLM classify fall back to the tabular heuristic:
    an ESN-named company scores below a direct-hire row."""
    esn = {
        "title": "Data Engineer",
        "description_text": "Mission data platform",
        "company": "Finax Consulting",
        "engagement_type": None,  # not yet LLM-classified
        "posting_company_type": "end_client",
        "company_info": {},
    }
    direct = {
        "title": "Data Engineer",
        "description_text": "Build data platform",
        "company": "Vibe.co",
        "engagement_type": None,
        "posting_company_type": "end_client",
        "company_info": {},
    }
    assert _score_company_quality(esn) < _score_company_quality(direct)
    assert _score_company_quality(direct) == 0.7  # 0.5 baseline + 0.2 direct


def test_company_quality_source_engagement_wins_over_heuristic():
    """Source-provided engagement (hiringcafe) is authoritative even when the
    heuristic would say consulting (e.g. 'Groupe' in the company name)."""
    job = {
        "title": "Data Engineer",
        "description_text": "text",
        "company": "Groupe A",
        "engagement_type": "direct",  # from the source
        "posting_company_type": "end_client",
        "company_info": {},
    }
    assert _score_company_quality(job) == 0.7


def test_company_quality_dim_join_enterprise_boost():
    job = {
        "title": "Data Engineer",
        "description_text": "text",
        "company": "Acme",
        "engagement_type": "direct",
        "posting_company_type": "end_client",
        "company_info": {"org_type": "enterprise", "stock_symbol": "ACME"},
    }
    # 0.5 + 0.2 direct + 0.1 enterprise + 0.05 stock = 0.85
    assert _score_company_quality(job) == 0.85


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
    today = date(2026, 8, 24)
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


def _bit_for_bit_fixture():
    """Fixed 10-job fixture spanning all scoring dimensions.

    Dates are computed relative to ``date.today()`` so the freshness
    component (which uses ``date.today()`` internally) is stable across
    runs: the test pins relative ages, not absolute dates.
    """
    today = date.today()
    return [
        {   # high pay, remote, modern stack, enterprise, fresh
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
        {   # ESN consulting by name, low pay, legacy stack, old post
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
        {   # hiringcafe direct enterprise, good pay, fresh
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
        {   # consulting_firm org, desc ESN signal, contract mission
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


# Ground truth frozen from the CURRENT score_jobs() implementation.
# Contract for the upcoming weight-config refactor: any change to weights,
# decay curves, or tier thresholds that shifts any score or ranking here
# MUST fail this test.
_FROZEN = {
    "J01": (0.826, "top", {"pay": 1.0, "flexibility": 0.8, "low_responsibility": 0.5,
                           "tech_match": 1.0, "company_quality": 0.85, "freshness": 0.989}),
    "J05": (0.761, "top", {"pay": 1.0, "flexibility": 0.8, "low_responsibility": 0.45,
                           "tech_match": 0.75, "company_quality": 0.7999999999999999, "freshness": 0.972}),
    "J09": (0.69, "high", {"pay": 0.3, "flexibility": 1.0, "low_responsibility": 0.65,
                           "tech_match": 1.0, "company_quality": 0.7, "freshness": 1.0}),
    "J03": (0.618, "high", {"pay": 0.4, "flexibility": 0.65, "low_responsibility": 0.85,
                            "tech_match": 1.0, "company_quality": 0.7, "freshness": 0.919}),
    "J06": (0.531, "medium", {"pay": 0.6, "flexibility": 0.9500000000000001, "low_responsibility": 0.5,
                              "tech_match": 1.0, "company_quality": 0.4, "freshness": 0.75}),
    "J10": (0.449, "low", {"pay": 0.8, "flexibility": 0.65, "low_responsibility": 0.0,
                           "tech_match": 1.0, "company_quality": 0.7, "freshness": 0.722}),
    "J04": (0.447, "low", {"pay": 0.3, "flexibility": 0.5, "low_responsibility": 0.0,
                           "tech_match": 1.0, "company_quality": 0.85, "freshness": 0.994}),
    "J08": (0.257, "low", {"pay": 0.8, "flexibility": 0.5, "low_responsibility": 0.5,
                           "tech_match": 1.0, "company_quality": 0.7, "freshness": 0.375}),
    "J07": (0.211, "low", {"pay": 0.2, "flexibility": 0.5, "low_responsibility": 0.65,
                           "tech_match": 0.25, "company_quality": 0.7, "freshness": 0.5}),
    "J02": (0.143, "low", {"pay": 0.2, "flexibility": 0.7999999999999999, "low_responsibility": 0.65,
                           "tech_match": 0.25, "company_quality": 0.5, "freshness": 0.3}),
}


def test_job_score_bit_for_bit():
    """Freeze current score_jobs() output bit-for-bit.

    Regression gate for the WS1 weight-config refactor: the ordering by
    overall_score, every overall_score, recommendation_tier, and the full
    per-dimension scores dict must reproduce the frozen values exactly.
    """
    jobs = _bit_for_bit_fixture()
    scored = score_jobs(jobs)

    assert [j["job_id"] for j in scored] == [
        "J01", "J05", "J09", "J03", "J06", "J10", "J04", "J08", "J07", "J02",
    ]
    for job in scored:
        expected_score, expected_tier, expected_scores = _FROZEN[job["job_id"]]
        assert job["overall_score"] == expected_score
        assert job["recommendation_tier"] == expected_tier
        assert job["scores"] == expected_scores
