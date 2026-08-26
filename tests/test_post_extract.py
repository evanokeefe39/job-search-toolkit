"""Unit tests for job_search_toolkit.linkedin.post_extract.

Covers the verdict rules (land / queue / drop), every extractor, the salary
format matrix (k / € / NBSP / TJM), multi-word role precedence, workplace and
contract vocabularies, and the fixed ``PostExtraction`` contract.
"""

import pytest

from job_search_toolkit.linkedin.models import PostRecord
from job_search_toolkit.linkedin.post_extract import (
    detect_language,
    extract_contract_duration,
    extract_contract_types,
    extract_end_client,
    extract_engagement,
    extract_from_post,
    extract_location,
    extract_region,
    extract_salary,
    extract_seniority,
    extract_title,
    extract_workplace,
    extract_years_experience,
)
from job_search_toolkit.schemas import (
    ContractType,
    EngagementType,
    SeniorityLevel,
    WorkplaceType,
)


def _post(text: str, author: str = "Acme") -> PostRecord:
    return PostRecord(
        post_url="https://www.linkedin.com/posts/-activity-123",
        author_name=author,
        author_profile_url=None,
        date_published=None,
        text=text,
        technologies=[],
        likes=None,
        activity_id="123",
        name_from_slug=False,
        content_quality="full",
    )


# --- verdict: land ----------------------------------------------------------


def test_hiring_post_lands_with_title_and_location():
    res = extract_from_post(_post("We're hiring a Senior Data Engineer in Paris"))
    assert res["verdict"] == "land"
    assert res["title"] == "Senior Data Engineer"
    assert res["location_raw"] == "Paris"


def test_hiring_post_lands_with_title_and_workplace():
    res = extract_from_post(_post("We're hiring a Senior Data Engineer, fully remote"))
    assert res["verdict"] == "land"
    assert res["title"] == "Senior Data Engineer"
    assert res["workplace_type"] == WorkplaceType.REMOTE


def test_french_hiring_post_lands():
    res = extract_from_post(
        _post("Nous recrutons un ingénieur data expérimenté basé à Lyon")
    )
    assert res["verdict"] == "land"
    assert "ingénieur" in res["title"].lower()
    assert "data" in res["title"].lower()
    assert res["location_raw"] == "Lyon"


# --- verdict: queue ---------------------------------------------------------


def test_role_noun_without_location_queues():
    res = extract_from_post(_post("Looking for a Data Engineer"))
    assert res["verdict"] == "queue"
    assert res["title"] is None
    assert res["location_raw"] is None


def test_hiring_verb_without_role_queues():
    res = extract_from_post(_post("We're hiring! Check our careers page"))
    assert res["verdict"] == "queue"
    assert res["title"] is None


# --- verdict: drop ----------------------------------------------------------


def test_non_job_post_drops():
    res = extract_from_post(_post("Had a great lunch with the team today"))
    assert res["verdict"] == "drop"


def test_empty_post_drops():
    res = extract_from_post(_post("   "))
    assert res["verdict"] == "drop"


# --- title ------------------------------------------------------------------


def test_multiword_role_not_captured_as_single_suffix():
    res = extract_from_post(_post("We're hiring a Data Engineer in Paris"))
    assert res["title"] == "Data Engineer"


def test_longest_domain_claims_full_phrase():
    res = extract_from_post(
        _post("Hiring a Senior Business Intelligence Analyst in London")
    )
    assert res["title"] == "Senior Business Intelligence Analyst"


def test_bare_role_is_low_confidence():
    val, conf = extract_title("Data Engineer")
    assert val == "Data Engineer"
    assert conf == "low"


def test_title_none_for_no_role():
    assert extract_title("Just sharing a photo from the office") is None


def test_poste_title_context():
    res = extract_from_post(_post("Poste de développeur backend à Nantes"))
    assert res["verdict"] == "land"
    assert res["title"] == "développeur backend"


# --- location ---------------------------------------------------------------


def test_location_preposition_high_confidence():
    val, conf = extract_location("based in Berlin, we build data pipelines")
    assert val == "Berlin"
    assert conf == "high"


def test_location_french_preposition():
    val, conf = extract_location("Poste basé à Marseille")
    assert val == "Marseille"
    assert conf == "high"


def test_bare_city_low_confidence():
    val, conf = extract_location("Data Engineer Paris")
    assert val == "Paris"
    assert conf == "low"


def test_location_none_when_no_city():
    assert extract_location("No location mentioned here") is None


# --- workplace --------------------------------------------------------------


def test_workplace_remote():
    val, conf = extract_workplace("We offer 100% remote work")
    assert val == WorkplaceType.REMOTE
    assert conf == "high"


def test_workplace_remote_first_prefers_long_form():
    val, _ = extract_workplace("This is a remote-first team")
    assert val == WorkplaceType.REMOTE


def test_workplace_hybrid():
    val, _ = extract_workplace("Hybrid role in Paris")
    assert val == WorkplaceType.HYBRID


def test_workplace_french_hybride():
    val, _ = extract_workplace("Mission hybride")
    assert val == WorkplaceType.HYBRID


def test_workplace_onsite():
    val, _ = extract_workplace("On-site only")
    assert val == WorkplaceType.ONSITE


def test_workplace_telletravail():
    val, _ = extract_workplace("Télétravail possible")
    assert val == WorkplaceType.REMOTE


# --- salary ----------------------------------------------------------------


def test_salary_k_euro():
    val, conf = extract_salary("Salary 80k€")
    assert conf == "high"
    assert val["min_annual_eur"] == 80_000
    assert val["max_annual_eur"] == 80_000
    assert val["is_disclosed"] is True
    assert val["currency_original"] == "EUR"


def test_salary_leading_currency():
    val, _ = extract_salary("Compensation €80,000 per year")
    assert val["min_annual_eur"] == 80_000
    assert val["max_annual_eur"] == 80_000
    assert val["currency_original"] == "EUR"


def test_salary_french_nbsp_thousands():
    val, _ = extract_salary("Salaire 80\u202f000€ brut")
    assert val["min_annual_eur"] == 80_000


def test_salary_space_thousands():
    val, _ = extract_salary("Salaire 80 000€ brut")
    assert val["min_annual_eur"] == 80_000


def test_salary_range_k():
    val, _ = extract_salary("Salaire 80-100k")
    assert val["min_annual_eur"] == 80_000
    assert val["max_annual_eur"] == 100_000


def test_salary_range_k_with_spaces_and_currency():
    val, _ = extract_salary("Salaire 80k - 100k €")
    assert val["min_annual_eur"] == 80_000
    assert val["max_annual_eur"] == 100_000


def test_salary_tjm_day_rate_annualized():
    val, _ = extract_salary("TJM 600€/jour")
    assert val["frequency_original"] == "daily"
    assert val["min_annual_eur"] == 600 * 220


def test_salary_none_when_absent():
    assert extract_salary("No pay information provided") is None


# --- contract types ---------------------------------------------------------


def test_contract_cdi():
    val, conf = extract_contract_types("Contrat CDI à pourvoir")
    assert conf == "high"
    assert val == [ContractType.FULL_TIME]


def test_contract_freelance():
    val, _ = extract_contract_types("Freelance mission, 6 months")
    assert ContractType.CONTRACT in val


def test_contract_cdd():
    val, _ = extract_contract_types("CDD de 12 mois")
    assert val == [ContractType.CONTRACT]


def test_contract_none_when_absent():
    assert extract_contract_types("A sunny rooftop party") is None


# --- seniority --------------------------------------------------------------


def test_seniority_senior():
    val, conf = extract_seniority("Hiring a Senior Data Engineer in Paris")
    assert val == SeniorityLevel.SENIOR
    assert conf == "high"


def test_seniority_lead():
    val, _ = extract_seniority("We're hiring a Lead Data Engineer")
    assert val == SeniorityLevel.LEAD


def test_seniority_junior():
    val, _ = extract_seniority("Junior analyst position")
    assert val == SeniorityLevel.JUNIOR


def test_seniority_none_when_absent():
    assert extract_seniority("We need help with data") is None


# --- years of experience ----------------------------------------------------


def test_years_min():
    assert extract_years_experience("5+ years experience") == (5, "high")


def test_years_french():
    assert extract_years_experience("5+ ans d'expérience") == (5, "high")


def test_years_range_uses_minimum():
    assert extract_years_experience("5-8 years") == (5, "high")


def test_years_none_when_absent():
    assert extract_years_experience("Open to everyone") is None


# --- contract duration ------------------------------------------------------


def test_duration_months():
    assert extract_contract_duration("6 months contract") == ("6 months", "high")


def test_duration_french_mois():
    assert extract_contract_duration("CDD de 12 mois") == ("12 mois", "high")


def test_duration_none_when_absent():
    assert extract_contract_duration("Long term engagement") is None


# --- end client -------------------------------------------------------------


def test_end_client_english():
    val, conf = extract_end_client("Long term mission for client Acme Bank")
    assert conf == "high"
    assert val == "Acme Bank"


def test_end_client_french():
    val, _ = extract_end_client("Mission chez notre client Société Générale")
    assert val == "Société Générale"


def test_end_client_none_when_absent():
    assert extract_end_client("No client mentioned") is None


# --- engagement -------------------------------------------------------------


def test_engagement_consulting_from_author():
    val, _ = extract_engagement("Acme Consulting", "We are hiring")
    assert val == EngagementType.CONSULTING


def test_engagement_consulting_from_text():
    val, _ = extract_engagement("Acme", "Mission chez notre client final")
    assert val == EngagementType.CONSULTING


def test_engagement_direct():
    val, _ = extract_engagement("Acme", "We're hiring a Data Engineer")
    assert val == EngagementType.DIRECT


def test_engagement_unknown():
    assert extract_engagement("Acme", "Nice sunny day in the office") is None


# --- language ---------------------------------------------------------------


def test_language_french_when_any_function_word():
    assert detect_language("Nous recrutons un ingénieur data") == "fr"


def test_language_english_by_default():
    assert detect_language("We're hiring a Senior Data Engineer in Paris") == "en"


def test_language_french_function_word_in_english():
    assert detect_language("Looking for a senior developer, sur Paris") == "fr"


# --- extract_from_post contract shape ---------------------------------------


def test_extraction_contract_keys():
    res = extract_from_post(_post("We're hiring a Data Engineer in Paris"))
    assert set(res.keys()) == {
        "verdict",
        "title",
        "location_raw",
        "workplace_type",
        "salary",
        "contract_types",
        "seniority_level",
        "years_experience_min",
        "contract_duration",
        "end_client_name",
        "engagement_type",
        "description_language",
    }


def test_drop_still_populates_non_null_fields():
    res = extract_from_post(_post("Just a nice photo of our rooftop"))
    assert res["verdict"] == "drop"
    assert res["contract_types"] == []
    assert res["description_language"] == "en"


def test_queue_fills_found_fields_but_empties_title_location():
    res = extract_from_post(
        _post("Freelance CDI mission, 80k€, 5+ years, for client Acme")
    )
    assert res["verdict"] == "queue"
    assert res["title"] is None
    assert res["location_raw"] is None
    assert ContractType.CONTRACT in res["contract_types"]
    assert res["salary"]["min_annual_eur"] == 80_000
    assert res["years_experience_min"] == 5
    assert res["end_client_name"] == "Acme"


@pytest.mark.parametrize(
    "text,expected_title,expected_location",
    [
        ("We're hiring a Senior Data Engineer in Paris", "Senior Data Engineer", "Paris"),
        ("Hiring a Data Analyst in London", "Data Analyst", "London"),
        ("Recrutons un Lead ML Engineer à Berlin", "Lead ML Engineer", "Berlin"),
        ("We are seeking a Staff Data Scientist in New York", "Staff Data Scientist", "New York"),
    ],
)
def test_landing_matrix(text, expected_title, expected_location):
    res = extract_from_post(_post(text))
    assert res["verdict"] == "land"
    assert res["title"] == expected_title
    assert res["location_raw"] == expected_location


# --- Region extraction + region-completes-land verdict ---

def test_extract_region_words_and_acronyms():
    assert extract_region("Hiring Data Engineers across EMEA") == ("EMEA", "low")
    assert extract_region("Recruiting data engineers in Europe") == ("Europe", "low")
    assert extract_region("CDI data engineer en France") == ("France", "low")
    # acronyms must be uppercase (avoid the English pronoun/word "us"/"uk")
    assert extract_region("hiring in apac") is None
    assert extract_region("hiring across APAC") == ("APAC", "low")


def test_extract_region_none_when_no_region():
    assert extract_region("hiring a data engineer for a banking client") is None
    assert extract_region("") is None


def test_france_relevant_region_completes_land_without_city():
    res = extract_from_post(_post("Hiring a Data Engineer across Europe, Azure stack"))
    assert res["verdict"] == "land"
    assert res["title"] == "Data Engineer"
    assert res["location_raw"] == "Europe"


def test_emea_region_completes_land():
    res = extract_from_post(_post("We are hiring a Data Engineer for our EMEA clients"))
    assert res["verdict"] == "land"
    assert res["location_raw"] == "EMEA"


def test_non_france_region_does_not_complete_land():
    # APAC/USA exclude France -> not a France lead -> stays queue
    res = extract_from_post(_post("Hiring a Data Engineer in APAC"))
    assert res["verdict"] == "queue"
    assert res["location_raw"] is None


def test_region_does_not_override_city():
    res = extract_from_post(_post("Data Engineer in Paris, serving EMEA clients"))
    assert res["verdict"] == "land"
    assert res["location_raw"] == "Paris"
