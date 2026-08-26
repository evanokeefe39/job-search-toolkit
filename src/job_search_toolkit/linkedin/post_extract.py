"""Deterministic regex extraction for LinkedIn recruiter posts.

Turns an unstructured ``PostRecord`` body into structured fields (role title,
location, workplace, salary, contract, seniority, …) so posts can be normalized
into the canonical job schema without an LLM. Pure module: no I/O, no Dagster,
no network, no LLM — mirroring ``linkedin/tech_scan.py``'s style (precompiled
``re.IGNORECASE`` patterns, longest-first alternation, ``\\b`` boundaries, French
NBSP handling).

Matching rules (fixed by the build spec in ``tasks/plans/linkedin-post-to-job.md``):
- ``ROLE`` alternation is longest-first so a multi-word domain ("business
  intelligence", "power bi") claims its full phrase before a shorter domain
  could, and "Data Engineer" is captured whole rather than as just "Engineer".
  The role noun may be preceded by a domain ("data engineer") or followed by
  one ("ingénieur data"), covering French phrasing.
- Salary regexes handle all required formats: "80k€", "€80,000", "80 000€"
  (with French narrow NBSP ``\\u202f``), "80-100k", "80k - 100k €", and the TJM
  day-rate "600€/jour". All numeric parsing strips ``\\u202f`` / ``\\xa0`` /
  spaces before converting (see ``tasks/lessons.md``).
- ``description_language`` follows the rule: any French function word at all →
  ``"fr"``, else ``"en"``.
"""

import re
from typing import Literal, TypedDict

from job_search_toolkit.linkedin.models import PostRecord
from job_search_toolkit.schemas import (
    ContractType,
    EngagementType,
    Salary,
    SeniorityLevel,
    WorkplaceType,
)


# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------

Confidence = Literal["high", "low"]


class PostExtraction(TypedDict):
    verdict: Literal["land", "queue", "drop"]
    title: str | None
    location_raw: str | None
    workplace_type: WorkplaceType | None
    salary: Salary | None
    contract_types: list[ContractType]
    seniority_level: SeniorityLevel | None
    years_experience_min: int | None
    contract_duration: str | None
    end_client_name: str | None
    engagement_type: EngagementType | None
    description_language: str


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Seniority markers, longest-first for deterministic matching.
SENIORITY_TERMS = (
    "expérimenté", "principal", "director", "manager", "débutant", "confirmé",
    "sénior", "junior", "senior", "staff", "sr.", "lead", "head", "sr", "mid",
)

# Role domains (longest-first so "business intelligence" claims the phrase
# before "business"/"bi" could), plus the role noun. The noun may sit after a
# domain ("data engineer") or before it ("ingénieur data").
ROLE_DOMAINS = (
    "business intelligence", "machine learning", "power bi", "big data",
    "data", "analytics", "platform", "software", "database", "frontend",
    "backend", "devops", "tableau", "mlops", "dataops", "cloud", "etl",
    "sql", "ml", "bi", "ai",
)
ROLE_NORMS = (
    "administrateurs", "administrateur", "ingénieurs", "ingénieur",
    "développeurs", "développeur", "scientists", "scientist", "developers",
    "developer", "architects", "architect", "consultants", "consultant",
    "specialists", "specialist", "experts", "expert", "engineers", "engineer",
    "analysts", "analyst", "managers", "manager", "leads", "lead",
)

# Cities / regions, longest-first.
CITY_TERMS = (
    "seine-saint-denis", "île-de-france", "ile-de-france", "hauts-de-seine",
    "new york", "essonne", "strasbourg", "grenoble", "marseille", "toulouse",
    "bordeaux", "amsterdam", "bruxelles", "nantes", "berlin", "london",
    "paris", "lyon", "lille",
)

# Location prepositions, longest-first.
_LOCATION_PREPS = ("sur place à", "basée à", "based in", "basé à", "à", "in")

# Hiring-context verbs for high-confidence titles and the DROP gate.
_HIRING_VERBS = (
    "à la recherche d'un", "searching for", "rejoignez-nous comme", "looking for",
    "on cherche", "recrutons", "recherchons", "join us as", "recruiting",
    "en tant que", "recherche", "recrute", "seeking", "hiring",
)

# A hiring signal (job intent) — used by the DROP gate per the spec.
_HIRING_SIGNAL_TERMS = (
    "hiring", "recrute", "recrutons", "recherchons", "looking for", "seeking",
    "cdi", "cdd", "mission", "poste",
)

# Contract vocabulary, longest-first.
CONTRACT_TERMS = (
    "portage salarial", "fixed-term", "fixed term", "full-time", "full time",
    "part-time", "part time", "indépendant", "alternance", "internship",
    "intérim", "permanent", "freelance", "independant", "mission", "contract",
    "interim", "stage", "cdd", "cdi",
)

# Workplace vocabulary, longest-first (bare "remote" must come after longer
# forms so "remote-first" claims the whole token).
WORKPLACE_TERMS = (
    "remote-first", "100% remote", "full remote", "télétravail", "présentiel",
    "on-site", "sur site", "onsite", "hybride", "hybrid", "remote",
)

# ESN / direct engagement signals (mirrors score_engine.detect_engagement).
_ESN_NAME_SIGNALS = (
    "consulting", "conseil", "esn", "ssii", "recruitment", "staffing",
    "groupe", "holding",
)
_ESN_DESC_SIGNALS = ("chez notre client", "mission chez", "en mission", "client final")
_DIRECT_SIGNALS = (
    "we are hiring", "we're hiring", "nous recrutons", "join our team",
    "rejoignez notre", "on recrute", "notre équipe",
)

# French function words — any single hit flips the language to French.
_FR_FUNCTION_WORDS = (
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "ou", "pour",
    "avec", "dans", "sur", "nous", "vous", "notre", "votre", "au", "aux",
    "qui", "que", "est", "sont", "chez", "par", "plus", "moins", "très",
    "mais", "donc", "ce", "cette", "ces",
)

# ---------------------------------------------------------------------------
# Pattern builders
# ---------------------------------------------------------------------------


def _alt(*terms: str) -> str:
    """Join literal tokens into a longest-first alternation."""
    return "|".join(sorted(terms, key=len, reverse=True))


def _bounded(*terms: str) -> str:
    """Alternation with word-boundary guards (no trailing-word-char match)."""
    return rf"\b(?:{_alt(*terms)})(?![a-zà-ÿ0-9])"


_DOMAIN = _alt(*ROLE_DOMAINS)
_NORM = _alt(*ROLE_NORMS)
# Domain(s) before and/or after the role noun, with outer word boundaries.
_ROLE = rf"\b(?:(?:{_DOMAIN})\s*)*(?:{_NORM})(?:\s*(?:{_DOMAIN}))*(?![a-zà-ÿ0-9])"
_SENIORITY = _bounded(*SENIORITY_TERMS)
_CITY = _bounded(*CITY_TERMS)

# ---------------------------------------------------------------------------
# Precompiled extractors
# ---------------------------------------------------------------------------

# Title, in a hiring context (high confidence).
_TITLE_HIRING = re.compile(
    rf"\b(?:{_alt(*_HIRING_VERBS)})\s+"
    rf"(?:a |an |un |une )?(?P<sen>{_SENIORITY}\s+)?(?P<role>{_ROLE})",
    re.IGNORECASE,
)

# Title, via an explicit post/role marker (high confidence).
_TITLE_POST = re.compile(
    rf"\b(?:poste|role|position|mission)\s+(?:de |of |for |pour )?"
    rf"(?:un |une )?(?P<sen>{_SENIORITY}\s+)?(?P<role>{_ROLE})",
    re.IGNORECASE,
)

# Bare role noun, anywhere (low confidence).
_BARE_ROLE = re.compile(
    rf"(?P<role>{_ROLE})\s*\(?(?:h/?f)?\)?",
    re.IGNORECASE,
)

# Location, from a preposition + city (high) or a bare city mention (low).
_PREP_LOCATION = re.compile(
    rf"\b(?:{_alt(*_LOCATION_PREPS)})\s+(?P<loc>{_CITY})",
    re.IGNORECASE,
)
_BARE_CITY = re.compile(rf"(?P<loc>{_CITY})", re.IGNORECASE)

# Recruiter-managed regions. Multi-word/word regions are matched
# case-insensitively; acronyms (EMEA/APAC/...) must appear UPPERCASE so the
# English pronoun "us" or "uk" (a word) never matches "US"/"UK".
_REGION_WORDS = ("north america", "united states", "nordics", "europe", "france")
_REGION_ACRONYMS = ("EMEA", "APAC", "DACH", "MENA", "LATAM", "BENELUX", "USA", "UK")
# Regions that include France — only these are usable location signals for the
# France-focused pipeline (a post scoped to APAC/USA is not a France lead).
_FRANCE_RELEVANT_REGIONS = frozenset({"emea", "europe", "france"})
_REGION_WORD_RE = re.compile(rf"{_bounded(*_REGION_WORDS)}", re.IGNORECASE)
_REGION_ACRONYM_RE = re.compile(
    rf"\b(?:{'|'.join(_REGION_ACRONYMS)})\b"
)

# Workplace type.
_WORKPLACE = re.compile(rf"(?P<w>{_bounded(*WORKPLACE_TERMS)})", re.IGNORECASE)

# Salary. All patterns accept a French narrow NBSP / \xa0 / space / comma /
# dot as a thousands separator; currency may sit before or after the number.
_NUM = r"\d[\d\s\u202f\u00a0.,]*"
_SALARY_RANGE = re.compile(
    rf"(?P<min>{_NUM})(?P<minunit>[kK])?\s*[-–—]\s*(?P<max>{_NUM})"
    rf"\s*(?P<unit>[kK])?\s*(?P<cur>[€$])?",
    re.IGNORECASE,
)
_SALARY_TJM = re.compile(rf"(?P<val>{_NUM})\s*[€$]\s*/\s*(?:jour|day)", re.IGNORECASE)
_SALARY_K = re.compile(rf"(?P<val>{_NUM})\s*[kK]\s*[€$]?", re.IGNORECASE)
_SALARY_PLAIN = re.compile(rf"(?P<val>{_NUM})\s*[€$]", re.IGNORECASE)
_SALARY_LEAD = re.compile(rf"[€$]\s*(?P<val>{_NUM})", re.IGNORECASE)

# Contract types.
_CONTRACT = re.compile(rf"{_bounded(*CONTRACT_TERMS)}", re.IGNORECASE)

# Seniority.
_SENIORITY_RE = re.compile(rf"{_SENIORITY}", re.IGNORECASE)

# Years of experience: "5+ years", "5 ans", "5+ ans d'expérience", "5-8 years".
_YEARS_RANGE = re.compile(
    r"\b(?P<min>\d{1,2})\s*[-–]\s*\d{1,2}\s*(?:years?|ans)", re.IGNORECASE
)
_YEARS_MIN = re.compile(r"\b(?P<n>\d{1,2})\s*\+\s*(?:years?|ans)", re.IGNORECASE)

# Contract duration: "6 months", "6 mois", "12-month".
_DURATION = re.compile(r"\b(?P<n>\d{1,3})\s*(?:[-–]|\s+)\s*(?:mois|months?)", re.IGNORECASE)

# End client: "for client X", "pour le client X", "chez notre client X".
_END_CLIENT = re.compile(
    r"(?:for\s+(?:the\s+)?client|pour\s+(?:le\s+|la\s+)?client|"
    r"chez\s+notre\s+client|client\s+final)\s*[:–—-]?\s*"
    r"(?P<name>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 .&'’-]{1,40})",
    re.IGNORECASE,
)

# French function-word detection.
_FR_PATTERN = re.compile(rf"\b(?:{_alt(*_FR_FUNCTION_WORDS)})\b", re.IGNORECASE)

# DROP gate: any hiring signal at all.
_HIRING_SIGNAL = re.compile(rf"{_bounded(*_HIRING_SIGNAL_TERMS)}", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Enumerated-field maps
# ---------------------------------------------------------------------------

_CONTRACT_MAP: dict[str, ContractType] = {
    "cdi": ContractType.FULL_TIME,
    "permanent": ContractType.FULL_TIME,
    "full-time": ContractType.FULL_TIME,
    "full time": ContractType.FULL_TIME,
    "cdd": ContractType.CONTRACT,
    "fixed-term": ContractType.CONTRACT,
    "fixed term": ContractType.CONTRACT,
    "contract": ContractType.CONTRACT,
    "freelance": ContractType.CONTRACT,
    "indépendant": ContractType.CONTRACT,
    "independant": ContractType.CONTRACT,
    "portage salarial": ContractType.CONTRACT,
    "mission": ContractType.CONTRACT,
    "stage": ContractType.INTERNSHIP,
    "internship": ContractType.INTERNSHIP,
    "alternance": ContractType.INTERNSHIP,
    "intérim": ContractType.TEMPORARY,
    "interim": ContractType.TEMPORARY,
    "part-time": ContractType.PART_TIME,
    "part time": ContractType.PART_TIME,
}

_SENIORITY_MAP: dict[str, SeniorityLevel] = {
    "junior": SeniorityLevel.JUNIOR,
    "débutant": SeniorityLevel.JUNIOR,
    "mid": SeniorityLevel.MID,
    "senior": SeniorityLevel.SENIOR,
    "sr": SeniorityLevel.SENIOR,
    "sr.": SeniorityLevel.SENIOR,
    "confirmé": SeniorityLevel.SENIOR,
    "sénior": SeniorityLevel.SENIOR,
    "expérimenté": SeniorityLevel.SENIOR,
    "lead": SeniorityLevel.LEAD,
    "staff": SeniorityLevel.LEAD,
    "principal": SeniorityLevel.LEAD,
    "head": SeniorityLevel.LEAD,
    "director": SeniorityLevel.LEAD,
    "manager": SeniorityLevel.MANAGER,
}

_WORKPLACE_MAP: dict[str, WorkplaceType] = {
    "remote-first": WorkplaceType.REMOTE,
    "100% remote": WorkplaceType.REMOTE,
    "full remote": WorkplaceType.REMOTE,
    "télétravail": WorkplaceType.REMOTE,
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "hybride": WorkplaceType.HYBRID,
    "on-site": WorkplaceType.ONSITE,
    "onsite": WorkplaceType.ONSITE,
    "sur site": WorkplaceType.ONSITE,
    "présentiel": WorkplaceType.ONSITE,
}


# ---------------------------------------------------------------------------
# Extractors — each returns (value, Confidence) or None when absent.
# ---------------------------------------------------------------------------


def _clean_title(sen: str | None, role: str) -> str:
    """Join an optional seniority marker and role into a title string."""
    return " ".join(p.strip() for p in (sen, role) if p and p.strip())


def extract_title(text: str) -> tuple[str, Confidence] | None:
    """Extract the role title from post text.

    Preconditions: ``text`` is the raw post body (may be empty/whitespace).
    Postconditions: returns ``(title, confidence)`` where confidence is "high"
    when the role appears in a hiring/post context and "low" for a bare role
    noun; returns ``None`` when no role noun is present. Multi-word roles are
    captured whole (longest-first), never as a truncated suffix.
    """
    if not text:
        return None
    m = _TITLE_HIRING.search(text)
    if m:
        return _clean_title(m.group("sen"), m.group("role")), "high"
    m = _TITLE_POST.search(text)
    if m:
        return _clean_title(m.group("sen"), m.group("role")), "high"
    m = _BARE_ROLE.search(text)
    if m:
        return m.group("role").strip(), "low"
    return None


def extract_location(text: str) -> tuple[str, Confidence] | None:
    """Extract a city/region location from post text.

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(location, confidence)`` — "high" when the city
    follows a location preposition ("in", "à", "based in", …), "low" for a bare
    city mention; returns ``None`` when no known city appears.
    """
    if not text:
        return None
    m = _PREP_LOCATION.search(text)
    if m:
        return m.group("loc"), "high"
    m = _BARE_CITY.search(text)
    if m:
        return m.group("loc"), "low"
    return None


def extract_workplace(text: str) -> tuple[WorkplaceType, Confidence] | None:
    """Extract the workplace arrangement (remote/hybrid/onsite).

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(WorkplaceType, "high")`` when a remote/hybrid/
    onsite keyword appears, else ``None``. Longer forms ("remote-first",
    "100% remote") are preferred over the bare "remote" token.
    """
    if not text:
        return None
    m = _WORKPLACE.search(text)
    if not m:
        return None
    token = m.group("w").strip().lower()
    return _WORKPLACE_MAP.get(token), "high"


def extract_region(text: str) -> tuple[str, Confidence] | None:
    """Extract a recruiter-managed region (EMEA, APAC, DACH, Europe, France…).

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(region, confidence)`` for the first region
    mention (words case-insensitive, acronyms uppercase-only), else ``None``.
    The caller decides France-relevance via ``FRANCE_RELEVANT_REGIONS``.
    """
    if not text:
        return None
    m = _REGION_WORD_RE.search(text)
    if m:
        return m.group(0).strip().title(), "low"
    m = _REGION_ACRONYM_RE.search(text)
    if m:
        return m.group(0), "low"
    return None


def _to_int(raw: str) -> int:
    """Convert a raw salary digit string to an int, stripping thousands
    separators (space, ``\\u202f``, ``\\xa0``, comma, dot) first."""
    cleaned = "".join(ch for ch in raw if ch not in " \u202f\u00a0.,")
    return int(float(cleaned))


def _currency(cur: str | None, matched: str, default: str = "€") -> str:
    """Resolve a currency symbol to an ISO-ish code (EUR/USD)."""
    sym = cur if cur else (default if default in matched else "$")
    return "EUR" if sym == "€" else "USD"


def extract_salary(text: str) -> tuple[Salary, Confidence] | None:
    """Extract compensation into a normalized annual-EUR ``Salary``.

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(Salary, "high")`` for any of the supported
    formats ("80k€", "€80,000", "80 000€", "80-100k", "80k - 100k €",
    "600€/jour") and ``None`` when no salary is found. Daily TJM rates are
    annualized (×220 working days) so values are comparable as annual EUR.
    """
    if not text:
        return None

    m = _SALARY_RANGE.search(text)
    if m:
        # A "k" anywhere in the range scales both bounds (e.g. "80-100k").
        scale = 1000 if (m.group("minunit") or m.group("unit")) else 1
        lo = _to_int(m.group("min")) * scale
        hi = _to_int(m.group("max")) * scale
        return (
            Salary(
                min_annual_eur=lo,
                max_annual_eur=hi,
                currency_original=_currency(m.group("cur"), m.group(0)),
                frequency_original="yearly",
                is_disclosed=True,
            ),
            "high",
        )

    m = _SALARY_TJM.search(text)
    if m:
        daily = _to_int(m.group("val"))
        return (
            Salary(
                min_annual_eur=daily * 220,
                max_annual_eur=daily * 220,
                currency_original=_currency("€" if "€" in m.group(0) else "$", m.group(0)),
                frequency_original="daily",
                is_disclosed=True,
            ),
            "high",
        )

    m = _SALARY_K.search(text)
    if m:
        value = _to_int(m.group("val")) * 1000
        return (
            Salary(
                min_annual_eur=value,
                max_annual_eur=value,
                currency_original=_currency("€" if "€" in m.group(0) else "$", m.group(0)),
                frequency_original="yearly",
                is_disclosed=True,
            ),
            "high",
        )

    m = _SALARY_PLAIN.search(text)
    if m:
        value = _to_int(m.group("val"))
        return (
            Salary(
                min_annual_eur=value,
                max_annual_eur=value,
                currency_original=_currency("€" if "€" in m.group(0) else "$", m.group(0)),
                frequency_original="yearly",
                is_disclosed=True,
            ),
            "high",
        )

    m = _SALARY_LEAD.search(text)
    if m:
        value = _to_int(m.group("val"))
        sym = m.group(0).lstrip()[:1]
        return (
            Salary(
                min_annual_eur=value,
                max_annual_eur=value,
                currency_original=_currency("€" if sym == "€" else "$", m.group(0)),
                frequency_original="yearly",
                is_disclosed=True,
            ),
            "high",
        )

    return None


def extract_contract_types(text: str) -> tuple[list[ContractType], Confidence] | None:
    """Extract contract types (CDI/CDD/freelance/…).

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(list, "high")`` of deduplicated ``ContractType``
    values in first-occurrence order, or ``None`` when no contract keyword
    appears.
    """
    if not text:
        return None
    found: list[ContractType] = []
    for m in _CONTRACT.finditer(text):
        token = m.group(0).strip().lower()
        ct = _CONTRACT_MAP.get(token)
        if ct is not None and ct not in found:
            found.append(ct)
    return (found, "high") if found else None


def extract_seniority(text: str) -> tuple[SeniorityLevel, Confidence] | None:
    """Extract the seniority level (senior/lead/junior/…).

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(SeniorityLevel, confidence)`` where confidence
    is "high" when the marker appears inside a hiring/post title context,
    "low" otherwise; returns ``None`` when no seniority marker is found.
    """
    if not text:
        return None
    m = _SENIORITY_RE.search(text)
    if not m:
        return None
    level = _SENIORITY_MAP.get(m.group(0).strip().lower())
    if level is None:
        return None
    in_context = bool(_TITLE_HIRING.search(text) or _TITLE_POST.search(text))
    return level, ("high" if in_context else "low")


def extract_years_experience(text: str) -> tuple[int, Confidence] | None:
    """Extract the minimum years-of-experience requirement.

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(int, "high")`` for patterns like "5+ years",
    "5 ans", "5+ ans d'expérience", or the minimum bound of "5-8 years";
    returns ``None`` otherwise.
    """
    if not text:
        return None
    m = _YEARS_RANGE.search(text)
    if m:
        return int(m.group("min")), "high"
    m = _YEARS_MIN.search(text)
    if m:
        return int(m.group("n")), "high"
    return None


def extract_contract_duration(text: str) -> tuple[str, Confidence] | None:
    """Extract the contract duration (e.g. "6 months", "6 mois").

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(matched_text, "high")`` such as "6 months" or
    "6 mois", or ``None`` when no duration is present.
    """
    if not text:
        return None
    m = _DURATION.search(text)
    if not m:
        return None
    return m.group(0).strip(), "high"


def extract_end_client(text: str) -> tuple[str, Confidence] | None:
    """Extract the end-client name from consulting phrasing.

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns ``(name, "high")`` for patterns like "for client X"
    / "pour le client X" / "chez notre client X", trimmed of trailing
    punctuation; returns ``None`` when no end-client phrase is present.
    """
    if not text:
        return None
    m = _END_CLIENT.search(text)
    if not m:
        return None
    name = re.sub(r"[.,;!]+$", "", m.group("name").strip()).strip()
    return (name, "high") if name else None


def extract_engagement(author_name: str | None, text: str) -> tuple[EngagementType, Confidence] | None:
    """Classify engagement as consulting or direct from author/text signals.

    Preconditions: ``author_name`` is the posting entity name (may be None);
    ``text`` is the raw post body.
    Postconditions: returns ``(EngagementType, "high")`` — ``CONSULTING`` on an
    ESN/recruiter signal (name or body), ``DIRECT`` on a direct-hiring signal,
    else ``None`` (unknown, left for the enrichment LLM).
    """
    name = (author_name or "").lower()
    desc = (text or "").lower()
    if any(s in name for s in _ESN_NAME_SIGNALS) or any(s in desc for s in _ESN_DESC_SIGNALS):
        return EngagementType.CONSULTING, "high"
    if any(s in desc for s in _DIRECT_SIGNALS):
        return EngagementType.DIRECT, "high"
    return None


def detect_language(text: str) -> str:
    """Detect the post language: any French function word => "fr", else "en".

    Preconditions: ``text`` is the raw post body.
    Postconditions: returns "fr" when the text contains any French function
    word, "en" otherwise.
    """
    return "fr" if _FR_PATTERN.search(text or "") else "en"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def extract_from_post(post: PostRecord) -> PostExtraction:
    """Run every extractor over a post and apply the verdict rules.

    Preconditions: ``post`` is a ``PostRecord`` with a ``text`` field (may be
    empty) and an ``author_name`` field.

    Postconditions:
    - verdict "drop" — no role noun, no bare role match, and no hiring signal;
      excluded from the silver warehouse.
    - verdict "land" — a title was extracted AND a location or workplace was
      found; full fields are populated.
    - verdict "queue" — a hiring signal exists but title/location were not
      confidently extracted; title and location_raw are left ``None`` for the
      enrichment LLM pass, while other found fields are kept.
    """
    text = (post.get("text") or "").strip()

    title = extract_title(text)
    location = extract_location(text)
    workplace = extract_workplace(text)
    region = extract_region(text)
    region_ok = region is not None and region[0].lower() in _FRANCE_RELEVANT_REGIONS

    has_bare_role = bool(text and _BARE_ROLE.search(text))
    has_hiring_signal = bool(text and _HIRING_SIGNAL.search(text))

    if title is None and not has_bare_role and not has_hiring_signal:
        verdict: Literal["land", "queue", "drop"] = "drop"
    elif title is not None and (location is not None or workplace is not None or region_ok):
        verdict = "land"
    else:
        verdict = "queue"

    if verdict == "queue":
        out_title: str | None = None
        out_location: str | None = None
    else:
        out_title = title[0] if title else None
        # Region is a location-completing signal: use it when no city was found.
        out_location = location[0] if location is not None else (region[0] if region_ok else None)

    salary = extract_salary(text)
    contracts = extract_contract_types(text)
    seniority = extract_seniority(text)
    engagement = extract_engagement(post.get("author_name"), text)
    years = extract_years_experience(text)
    duration = extract_contract_duration(text)
    end_client = extract_end_client(text)

    return PostExtraction(
        verdict=verdict,
        title=out_title,
        location_raw=out_location,
        workplace_type=workplace[0] if workplace else None,
        salary=salary[0] if salary else None,
        contract_types=list(contracts[0]) if contracts else [],
        seniority_level=seniority[0] if seniority else None,
        years_experience_min=years[0] if years else None,
        contract_duration=duration[0] if duration else None,
        end_client_name=end_client[0] if end_client else None,
        engagement_type=engagement[0] if engagement else None,
        description_language=detect_language(text),
    )
