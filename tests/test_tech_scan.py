"""Unit tests for job_search_toolkit.linkedin.tech_scan.

Covers the deterministic, file-backed technology scanner:
- synonym / alias / abbreviation mapping to one canonical keyword
- spacing, singular/plural and case-insensitive variant matching
- word-boundary safety ("SQL" never matches "SQLite" or "MySQL")
- canonical assignment order (longest base first, alphabetical tie-break)
- first-occurrence dedup order and empty-input behaviour
- from_file parsing (comments, blanks, "base = v1, v2", bare lines, errors)

Run: uv run pytest tests/test_tech_scan.py
"""

from __future__ import annotations

import pytest

from job_search_toolkit.linkedin.tech_scan import (
    DEFAULT_SYNONYMS,
    DEFAULT_TECHNOLOGIES,
    TechnologyScanner,
)


# --- default contract --------------------------------------------------------


def test_default_technologies_exact_contract():
    assert DEFAULT_TECHNOLOGIES == [
        "Fabric",
        "Power BI",
        "Azure",
        "Synapse",
        "Data Factory",
        "Databricks",
        "Spark",
        "SQL",
        "Python",
        "dbt",
        "Snowflake",
        "BigQuery",
        "Airflow",
        "Kafka",
        "Kubernetes",
        "Docker",
        "Terraform",
        "Power Apps",
        "Power Automate",
        "Kusto",
        "Data Lake",
        "Delta Lake",
        "Tableau",
        "Dataiku",
    ]


def test_default_synonyms_exact_contract():
    assert DEFAULT_SYNONYMS == {
        "Fabric": ["Microsoft Fabric", "MS Fabric"],
        "Spark": ["PySpark", "Spark SQL", "Spark Streaming", "Apache Spark"],
        "Data Factory": ["ADF", "Azure Data Factory"],
        "Kusto": ["KQL"],
        "Power BI": ["PowerBI", "Power-BI", "PBI"],
        "Synapse": ["Azure Synapse", "Synapse Analytics"],
        "Kubernetes": ["K8s"],
        "SQL": ["T-SQL", "PL/SQL"],
        "Data Lake": ["Azure Data Lake", "ADLS"],
    }


# --- scan: aliases / abbreviations map to one canonical ----------------------


def test_scan_long_form_maps_to_canonical_not_alias():
    # Long form resolves to the canonical base only — never also the alias.
    assert TechnologyScanner.from_defaults().scan("Microsoft Fabric is hot") == ["Fabric"]


def test_scan_aliases_dedupe_to_one_canonical():
    assert TechnologyScanner.from_defaults().scan("PySpark and Spark SQL pipelines") == ["Spark"]


def test_scan_adf_and_full_name_dedupe_to_data_factory():
    assert TechnologyScanner.from_defaults().scan("ADF and Azure Data Factory") == ["Data Factory"]


def test_scan_hyphen_spacing_variant():
    assert TechnologyScanner.from_defaults().scan("power-bi dashboards") == ["Power BI"]


def test_scan_concatenated_spacing_variant():
    assert TechnologyScanner.from_defaults().scan("PowerBI") == ["Power BI"]


def test_scan_abbreviation_and_base_dedupe():
    assert TechnologyScanner.from_defaults().scan("k8s and Kubernetes") == ["Kubernetes"]


def test_scan_abbreviation_maps_to_canonical():
    assert TechnologyScanner.from_defaults().scan("KQL queries") == ["Kusto"]


# --- scan: word boundaries ---------------------------------------------------


def test_scan_sql_not_matched_inside_sqlite():
    assert TechnologyScanner.from_defaults().scan("Wrote SQL, not SQLite") == ["SQL"]


def test_scan_sql_not_matched_inside_mysql():
    assert TechnologyScanner.from_defaults().scan("Migrated off MySQL") == []


# --- scan: ordering and empty inputs -----------------------------------------


def test_scan_first_occurrence_order():
    text = "Microsoft Fabric and Azure and Power BI"
    assert TechnologyScanner.from_defaults().scan(text) == ["Fabric", "Azure", "Power BI"]


def test_scan_no_matches_returns_empty():
    assert TechnologyScanner.from_defaults().scan("nothing relevant here") == []


def test_scan_empty_text_returns_empty():
    assert TechnologyScanner.from_defaults().scan("") == []


def test_scan_is_case_insensitive():
    assert TechnologyScanner.from_defaults().scan("mIcRoSoFt FaBrIc") == ["Fabric"]


# --- canonical assignment order ----------------------------------------------


def test_longest_base_claims_shared_variant():
    # The longer base iterates first, so its synonym "Data" steals the
    # bare "data" variant from the shorter base "Data".
    scanner = TechnologyScanner(["Data", "Data Factory"], {"Data Factory": ["Data"]})
    assert scanner.scan("data") == ["Data Factory"]
    assert scanner.scan("data factory") == ["Data Factory"]


def test_alphabetical_tie_break_for_equal_length_bases():
    # Equal-length bases: "Alpha" iterates before "Zed" and claims "x" first.
    scanner = TechnologyScanner(["Zed", "Alpha"], {"Zed": ["x"], "Alpha": ["x"]})
    assert scanner.scan("x") == ["Alpha"]


def test_scan_without_synonyms_still_expands_spacing_and_plural():
    scanner = TechnologyScanner(["Power BI", "SQL"])
    assert scanner.scan("powerbi dashboards") == ["Power BI"]
    assert scanner.scan("wrote sqls") == ["SQL"]


def test_scan_with_empty_keywords_returns_empty():
    scanner = TechnologyScanner([])
    assert scanner.scan("Microsoft Fabric and Power BI") == []


# --- from_file ---------------------------------------------------------------


def test_from_file_parses_synonyms_comments_and_bare_lines(tmp_path):
    f = tmp_path / "tech.txt"
    f.write_text(
        "Fabric = Microsoft Fabric, MS Fabric\nAzure\n# c\nPower BI\n",
        encoding="utf-8",
    )
    scanner = TechnologyScanner.from_file(f)
    assert scanner.scan("MS Fabric on Azure") == ["Fabric", "Azure"]


def test_from_file_bare_line_still_expands_spacing(tmp_path):
    f = tmp_path / "tech.txt"
    f.write_text("Power BI\n", encoding="utf-8")
    scanner = TechnologyScanner.from_file(f)
    assert scanner.scan("powerbi") == ["Power BI"]


def test_from_file_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "tech.txt"
    f.write_text("# only a comment\n\n   \n", encoding="utf-8")
    assert TechnologyScanner.from_file(f).scan("Azure") == []


def test_from_file_preserves_keyword_order_in_variant_claiming(tmp_path):
    f = tmp_path / "tech.txt"
    # "Data Factory" listed first claims its variants before bare "Data".
    f.write_text("Data Factory = Data\nData\n", encoding="utf-8")
    scanner = TechnologyScanner.from_file(f)
    assert scanner.scan("data") == ["Data Factory"]


def test_from_file_missing_raises_file_not_found(tmp_path):
    missing = tmp_path / "does_not_exist.txt"
    with pytest.raises(FileNotFoundError):
        TechnologyScanner.from_file(missing)


def test_from_file_splits_on_first_equals(tmp_path):
    f = tmp_path / "tech.txt"
    f.write_text("Kusto = KQL = also kusto\n", encoding="utf-8")
    scanner = TechnologyScanner.from_file(f)
    # Everything after the FIRST "=" is one synonym value ("KQL = also kusto");
    # the whole phrase still resolves to the canonical base.
    assert scanner.scan("KQL = also kusto") == ["Kusto"]

# --- from_defaults -----------------------------------------------------------


def test_from_defaults_scan_microsoft_fabric():
    assert TechnologyScanner.from_defaults().scan("Microsoft Fabric") == ["Fabric"]
