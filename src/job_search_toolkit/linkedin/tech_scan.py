"""Deterministic, file-backed technology scanner for LinkedIn text.

Maps free-form mentions (aliases, abbreviations, spacing/singular variants) to
a canonical technology keyword using a pure string/regex pipeline — no LLM, no
network. It fills the ``technologies`` field of ``PostRecord`` /
``JobRecord`` (see ``models.py``).

Matching rules (fixed by the build spec):
- Variant expansion is deterministic: each keyword contributes itself, its
  lowercase form, spacing variants (``" "`` -> ``"-"``, ``""``, ``"."``), a
  singular/plural counterpart, and — for keywords with a ``synonyms`` entry —
  the same expansion for every synonym.
- A variant is claimed by the first base keyword that produces it; bases are
  processed longest-first (alphabetical tie-break) so a multi-word base like
  "Data Factory" claims its full-phrase variants before a shorter base could.
- Matching is case-insensitive and word-boundary-safe: "SQL" never matches
  inside "SQLite" or "MySQL".
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

# Canonical technology keywords, in contract order (also the tie-break order
# for equally-long bases during variant claiming).
DEFAULT_TECHNOLOGIES: list[str] = [
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

# Alias / fuzzy / abbreviation variants per canonical keyword.
DEFAULT_SYNONYMS: dict[str, list[str]] = {
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

# Matches nothing — used when a scanner has zero keywords so ``scan`` still
# returns ``[]`` instead of matching at every position.
_NOTHING = re.compile(r"(?!)")

# Spacing separators tried when expanding a lowercased keyword.
_SPACING_SEPARATORS = ("-", "", ".")


def _spacing_variants(lower: str) -> list[str]:
    """Return the spacing variants of an already-lowercased keyword.

    Post: returns the keyword with each space replaced by ``"-"``, ``""`` and
    ``"."`` in turn; a keyword without spaces yields only itself. Result is
    deduplicated and order is deterministic.
    """
    return list({lower.replace(" ", sep) for sep in _SPACING_SEPARATORS})


def _singular_plural(lower: str) -> str:
    """Return the singular/plural counterpart of an already-lowercased keyword.

    Post: returns ``lower[:-1]`` when ``lower`` ends with ``"s"``, else
    ``lower + "s"``.
    """
    return lower[:-1] if lower.endswith("s") else lower + "s"


def _expand_keyword(term: str) -> list[str]:
    """Expand one keyword (base or synonym) into its match variants.

    Post: returns ``[term, lower, *spacing(lower), singular_plural(lower)]``
    with all derived forms lowercased; duplicates removed, first occurrence
    kept.
    """
    lower = term.lower()
    forms = [term, lower, *_spacing_variants(lower), _singular_plural(lower)]
    seen: set[str] = set()
    unique: list[str] = []
    for form in forms:
        if form not in seen:
            seen.add(form)
            unique.append(form)
    return unique


class TechnologyScanner:
    """Scans text for canonical technology keywords via deterministic variants.

    The variant->canonical map and the compiled matcher are built once in
    ``__init__``, so every ``scan`` call is a pure, repeatable regex pass.
    """

    def __init__(
        self,
        keywords: Sequence[str],
        synonyms: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        """Build a scanner from keyword and synonym lists.

        Pre: ``keywords`` holds the canonical technology names (a keyword only
        receives synonym variants when it also appears as a key in
        ``synonyms``).
        Post: the compiled matcher is ready; ``scan`` is deterministic and
        returns ``[]`` for any text when ``keywords`` is empty.
        """
        synonym_map = synonyms or {}
        # Variant -> canonical base. Bases are processed longest-first
        # (alphabetical tie-break) and the FIRST base to produce a variant
        # claims it; later duplicates are dropped.
        canonical_by_variant: dict[str, str] = {}
        for base in sorted(keywords, key=lambda b: (-len(b), b)):
            for form in _expand_keyword(base):
                canonical_by_variant.setdefault(form.lower(), base)
            for synonym in synonym_map.get(base, ()):
                for form in _expand_keyword(synonym):
                    canonical_by_variant.setdefault(form.lower(), base)

        # Case-insensitive, word-boundary-wrapped alternation, longest
        # alternative first so "microsoft fabric" matches before "fabric".
        variants = sorted(canonical_by_variant, key=lambda v: (-len(v), v))
        if variants:
            pattern = "(?i)" + "|".join(
                r"\b" + re.escape(v) + r"\b" for v in variants
            )
            self._pattern = re.compile(pattern)
        else:
            self._pattern = _NOTHING
        self._canonical_by_variant = canonical_by_variant

    @classmethod
    def from_file(cls, path: str | Path) -> "TechnologyScanner":
        """Load keywords and synonyms from a config file.

        File format: one keyword per line; blank lines and lines starting with
        ``#`` are ignored; ``base = v1, v2`` (split on the FIRST ``=``) maps
        ``base`` to synonyms ``v1, v2`` (each stripped); a bare line is a
        keyword with no synonyms (it still gets spacing/singular expansion).
        Keyword order in the file is preserved.

        Pre: ``path`` names a readable UTF-8 text file.
        Post: returns a scanner built from the parsed keywords/synonyms;
        raises ``FileNotFoundError`` (not swallowed) when the file is missing
        or unreadable, so callers can fall back to defaults.
        """
        p = Path(path)
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            raise
        except OSError as exc:  # e.g. permission error / not a file
            raise FileNotFoundError(f"cannot read technology list {path!r}: {exc}") from exc

        keywords: list[str] = []
        synonyms: dict[str, list[str]] = {}
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                base, _, rest = line.partition("=")
                base = base.strip()
                if not base:  # degenerate "= v1" line — nothing to scan
                    continue
                syns = [s.strip() for s in rest.split(",") if s.strip()]
                keywords.append(base)
                synonyms[base] = syns
            else:
                keywords.append(line)
        return cls(keywords, synonyms)

    @classmethod
    def from_defaults(cls) -> "TechnologyScanner":
        """Return a scanner built from the built-in keyword/synonym lists.

        Post: equivalent to ``TechnologyScanner(DEFAULT_TECHNOLOGIES,
        DEFAULT_SYNONYMS)``.
        """
        return cls(DEFAULT_TECHNOLOGIES, DEFAULT_SYNONYMS)

    def scan(self, text: str) -> list[str]:
        """Return canonical technologies found in ``text``.

        Pre: none — any string is accepted.
        Post: each returned element is a canonical keyword from the scanner's
        keyword list; duplicates are removed; order follows the first
        occurrence of the matching variant in ``text``; returns ``[]`` when
        nothing matches (including empty text).
        """
        found: list[str] = []
        seen: set[str] = set()
        for match in self._pattern.finditer(text):
            canonical = self._canonical_by_variant[match.group(0).lower()]
            if canonical not in seen:
                seen.add(canonical)
                found.append(canonical)
        return found
