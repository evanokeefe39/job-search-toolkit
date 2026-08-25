"""Unit tests for the tabular company engagement heuristic (score_engine).

The engagement detector replaces the LLM classify signal on the ranking
path. These tests pin the documented patterns; the heuristic is expected to
be tuned from data (see score_engine.py).
"""

from __future__ import annotations

from datetime import date

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
