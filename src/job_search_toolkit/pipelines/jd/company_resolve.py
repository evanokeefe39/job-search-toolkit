"""Company golden-record resolution: ladder + dim dedup + alias registry.

``silver.dim_company`` is the GOLDEN RECORD: one row per real-world company,
keyed by ``company_id = sha1(normalized name)[:16]`` (same SHA-1 scheme as the
old per-board key, now board-independent). Every board-side name maps to its
golden row through ``silver.company_alias`` (alias_name PK -> company_id,
append-only history).

Resolution ladder (validated: P=1.00 on the auto-accept set, see
tasks/plans/company-canonical-dedup.md):

1. Exact — normalized name already in the alias registry.
2. Stem — legal-suffix/article-stripped token-stem match.
3. Fuzzy-auto — rapidfuzz ``token_set_ratio >= 95`` AND (token-same-or-concat
   OR non-generic subset-extra) → auto-merge.
4. Human review — typo band (>=85), generic-token subsets, board mangling.
5. Reject < 85.

Sentiment aggregation on merge (DECIDED): drop ``inconclusive`` (a no-data
state, not an opinion); > 1 distinct remaining -> ``mixed``; exactly 1 -> that
value; all inconclusive -> ``inconclusive``.

Derivation-version marker: ``silver.dim_company.dedup_version`` is written
beside the deduped rows so stale state under a future rule change is
detectable (never silent staleness).

This module is pure warehouse + deterministic fuzzy logic: ZERO LLM calls.
It is wired as a SEPARATE Dagster asset off the zero-LLM ranking graph
(see assets/company_resolve.py) — ``scored_jobs``/``ranked_csv`` never depend
on it. The incremental ``resolve_new_names`` pass resolves NEW names only
through the ladder and never re-resolves already-resolved rows.

STALE-FK WINDOW (documented reality, NOT fixed at upsert): new fact rows
from ``silver.upsert_run`` still carry per-board SHA-1 ``company_id`` keys
and do NOT touch the alias registry — golden re-keying happens only when
``company_names_resolved`` runs. Between a scrape and the next resolution
run, new job rows hold per-board ids until the resolution pass re-keys
them (the asset is run explicitly after ingest; it is never on the
``pipeline run`` ranking path).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict


import duckdb

DEDUP_VERSION = "golden-v1"

# Human review queue export (append-only; see write_review_queue / the
# review workflow section of the plan). Under data/ (gitignored).
REVIEW_CSV = "data/company_review_pairs.csv"

# Legal-suffix / article tokens stripped when building the stem key
# (~15-line inline strip; hence no cleanco dependency).
SUF_TOKENS = {
    "holdings", "holding", "group", "groupe", "inc", "llc", "ltd", "limited",
    "plc", "gmbh", "ag", "sa", "sas", "sarl", "bv", "nv", "spa", "oy", "ab",
    "asa", "aps", "kk", "pte", "pty", "ug", "srl", "sl", "kft", "zrt", "gk",
    "co", "corp", "corporation", "company", "technologies", "technology",
    "labs", "lab", "solutions", "software", "international", "france",
    "usa", "uk", "b", "v", "n", "s", "a", "r", "l", "p", "c",
}
ARTICLES = {"the", "le", "la", "les", "a", "an", "of", "in"}

# Generic tokens that must never trigger an auto-merge as the "extra" token
# of a subset match (one/king/sas/... are real company names on their own).
GENERIC_EXTRA = {
    "one", "king", "sas", "sap", "nine", "philips", "international", "ai",
}

FUZZY_AUTO_THRESHOLD = 95.0
REVIEW_THRESHOLD = 85.0
# Named spot-check pairs (reviewer finding F1): generic-subset / mangling
# twins that must ALWAYS reach a human decision, even when the narrowed
# classifier (typo band / mangling floor) would not queue them on score
# alone. Never auto-merged — surfaced in the review CSV export.
SPOT_CHECK_PAIRS = [
    ("mistral", "mistral ai"),
    ("alstom", "astorm"),
    ("inspiire", "inspire"),
    ("qube research technologies", "quberesearchandtechnologies"),
]

_DOT_TAILS = (
    r"( s a r l)$", r"( s a s)$", r"( s a)$", r"( s r l)$",
    r"( b v)$", r"( n v)$", r"( p t e)$", r"( p t y)$",
)


def norm(name: str) -> str:
    """NFKD, de-accent, lowercase, punctuation -> space, whitespace collapse."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_stem(s: str) -> str:
    """Legal-suffix/article-stripped token stem (see SUF_TOKENS/ARTICLES)."""
    joined = " ".join(str(s or "").split())
    for pat in _DOT_TAILS:
        joined = re.sub(pat, "", joined)
    toks = joined.split()
    while len(toks) > 1 and toks[-1] in SUF_TOKENS and len(toks[-1]) > 1:
        toks.pop()
    while len(toks) > 1 and toks[0] in ARTICLES:
        toks.pop(0)
    return " ".join(t for t in toks if t not in ARTICLES)


def golden_company_id(name: str) -> str:
    """Golden surrogate key: SHA-1 of the normalized name, hex [:16]."""
    return hashlib.sha1(norm(name).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Sentiment aggregation (DECIDED rule)
# ---------------------------------------------------------------------------

def aggregate_sentiment(sentiments) -> str:
    """Aggregate per-board ``news_sentiment`` values for one merged company.

    Drop ``inconclusive``; > 1 distinct remaining -> ``mixed``; exactly 1 ->
    that value; all inconclusive (or empty) -> ``inconclusive``.
    """
    distinct = {s for s in sentiments if s and s != "inconclusive"}
    if len(distinct) > 1:
        return "mixed"
    if len(distinct) == 1:
        return next(iter(distinct))
    return "inconclusive"


# ---------------------------------------------------------------------------
# Fuzzy-auto acceptance (plan: P=1.00 rule set)
# ---------------------------------------------------------------------------

def _same_or_concat(ta: set[str], tb: set[str]) -> bool:
    """Token sets equal, or equal as concatenated strings (any token split)."""
    if ta == tb:
        return True
    return "".join(sorted("".join(ta))) == "".join(sorted("".join(tb)))


def _subset_rule(ta: set[str], tb: set[str]) -> str | None:
    """Subset relationship classifier.

    ``subset-extra``: every extra token is non-generic and length >= 6
    (auto-merge). ``subset-generic-or-short``: a generic/short extra token —
    human review. None when the token sets are not in a subset relation.
    """
    if not (ta < tb or tb < ta):
        return None
    small, large = (ta, tb) if len(ta) < len(tb) else (tb, ta)
    extra = large - small
    if all(len(t) >= 6 and t not in GENERIC_EXTRA for t in extra):
        return "subset-extra"
    return "subset-generic-or-short"


def fuzzy_auto(name_a: str, name_b: str) -> tuple[float, str] | None:
    """Return (score, rule) when the pair auto-merges (P=1.00 rule set).

    Requires ``token_set_ratio >= 95`` AND (token-same-or-concat OR
    non-generic subset-extra). Typo band 85-95 and generic subsets are
    deliberately NOT auto-accepted — they queue for human review.
    """
    from rapidfuzz import fuzz

    a, b = norm(name_a), norm(name_b)
    score = fuzz.token_set_ratio(a, b)
    if score < FUZZY_AUTO_THRESHOLD:
        return None
    ta, tb = set(strip_stem(a).split()), set(strip_stem(b).split())
    if not ta or not tb:
        return None
    if _same_or_concat(ta, tb):
        return (score, "token-same-or-concat")
    if _subset_rule(ta, tb) == "subset-extra":
        return (score, "subset-extra")
    return None


def review_reason(name_a: str, name_b: str) -> tuple[float, str] | None:
    """Classify an ambiguous pair for the human review queue.

    Returns (score, reason) for pairs a human must decide, None when the pair
    auto-merges or is rejected outright (< 85 is never offered). Board-side
    mangling (e.g. ``alstom``/``astorm``) is queued at typo-band distance:
    the anagram branch requires score >= 85 (below that it is noise, e.g.
    'a team'/'meta' 40.0), and the 1-2 edit branch covers the 80-85 band so
    alstom/astorm (83.3) is still queued — never silently auto-rejected.
    """
    from rapidfuzz import fuzz

    a, b = norm(name_a), norm(name_b)
    ta, tb = set(strip_stem(a).split()), set(strip_stem(b).split())
    if not ta or not tb:
        return None
    score = fuzz.token_set_ratio(a, b)
    if (
        _same_or_concat(ta, tb)
        and "".join(ta) != "".join(tb)
        and score >= REVIEW_THRESHOLD
    ):
        # Same characters, different tokenization/letters: board mangling
        # (score floor: an anagram below the typo band is coincidence).
        return (score, "board-mangling")
    from rapidfuzz.distance import Levenshtein

    if (
        REVIEW_THRESHOLD > score >= 80.0
        and len(ta) == len(tb) == 1
        and 0 < Levenshtein.distance(a, b) <= 2
    ):
        # 1-2 edit mangle under the typo band (e.g. alstom/astorm at 83.3):
        # board mangling, always queued, never auto-rejected.
        return (score, "board-mangling")
    if score < REVIEW_THRESHOLD:
        return None
    if fuzzy_auto(a, b):
        return None
    rule = _subset_rule(ta, tb)
    if rule == "subset-generic-or-short":
        return (score, rule)
    # 85-95 with no structural rule: typo band — narrowed to single-token
    # misspellings (inspiire/inspire) or equal token multisets, so unrelated
    # multi-token companies ('1g link consulting'/'bk consulting' 87.0) do
    # not flood the queue.
    if len(ta) == len(tb) == 1 or sorted(ta) == sorted(tb):
        return (score, "typo-band")
    return None


def ensure_company_alias(con: duckdb.DuckDBPyConnection) -> None:
    """Create ``silver.company_alias`` (idempotent, additive-only)."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS silver.company_alias (
            alias_name VARCHAR PRIMARY KEY,
            company_id VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            confidence DOUBLE,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )


def load_alias_registry(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """alias_name (normalized) -> golden company_id."""
    return dict(
        con.execute(
            "SELECT alias_name, company_id FROM silver.company_alias"
        ).fetchall()
    )


def register_alias(
    con: duckdb.DuckDBPyConnection,
    alias_name: str,
    company_id: str,
    source: str,
    confidence: float | None = None,
) -> bool:
    """Append one alias row. Returns True when newly inserted (append-only;
    re-runs and re-resolutions of already-resolved rows are no-ops)."""
    before = con.execute(
        f"SELECT company_id FROM silver.company_alias "
        f"WHERE alias_name = '{norm(alias_name).replace(chr(39), chr(39) * 2)}'"
    ).fetchone()
    if before:
        return False
    n = norm(alias_name)
    conf = "NULL" if confidence is None else repr(float(confidence))
    con.execute(
        f"INSERT INTO silver.company_alias (alias_name, company_id, source, confidence) "
        f"VALUES ('{n}', '{company_id}', '{source}', {conf})"
    )
    return True


def resolve_name(
    con: duckdb.DuckDBPyConnection,
    name: str,
) -> tuple[str | None, str, float | None, str | None]:
    """Ladder for one NEW name against the durable registry.

    Returns ``(golden_id, source, confidence, partner)``. ``golden_id`` is
    None when the name must self-seed its own golden row (source ``review``
    means it also queues for human review). Already-resolved names short-
    circuit at exact — never re-resolved.
    """
    ensure_company_alias(con)
    n = norm(name)
    hit = con.execute(
        f"SELECT company_id FROM silver.company_alias WHERE alias_name = '{n}'"
    ).fetchone()
    if hit:
        return (hit[0], "exact", None, None)
    registry = load_alias_registry(con)
    s = strip_stem(n)
    if s and s != n and s in registry:
        return (registry[s], "stem", None, s)
    best: tuple[float, str, str] | None = None
    for alias in registry:
        res = fuzzy_auto(n, alias)
        if res and (best is None or res[0] > best[0]):
            best = (res[0], res[1], alias)
    if best:
        return (registry[best[2]], "fuzzy", best[0], best[2])
    return (None, "review", None, None)


# ---------------------------------------------------------------------------
# Dim dedup migration (one-time, idempotent)
# ---------------------------------------------------------------------------

def clusters_for_names(names: list[str]) -> list[list[str]]:
    """Union-find over raw names: exact -> stem -> fuzzy-auto ladder."""
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_norm: dict[str, list[str]] = defaultdict(list)
    for n in names:
        by_norm[norm(n)].append(n)
    for g in by_norm.values():
        for o in g[1:]:
            union(g[0], o)

    by_stem: dict[str, list[str]] = defaultdict(list)
    for n in names:
        stem = strip_stem(norm(n))
        if stem:
            by_stem[stem].append(n)
    for g in by_stem.values():
        for o in g[1:]:
            union(g[0], o)

    roots = {find(n) for n in names}
    for root in roots:
        members = sorted(n for n in names if find(n) == root)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if fuzzy_auto(a, b):
                    union(a, b)

    out: dict[str, list[str]] = defaultdict(list)
    for n in names:
        out[find(n)].append(n)
    return [sorted(v) for v in out.values()]


_DIM_COLS = [
    "company_id", "name", "display_name", "source_board", "industry",
    "size_employees", "year_founded", "hq_country", "org_type", "company_type",
    "stock_symbol", "stock_exchange", "latest_funding_type",
    "latest_funding_amount_usd", "homepage_url", "enriched_at",
    "enrichment_version", "news_notes", "news_sentiment",
    "news_checked_at", "insee_employee_range", "insee_legal_type",
    "insee_checked_at",
]


def _lit(v) -> str:
    """SQL literal (NULL-safe; duckdb binding deadlock on Windows — repo rule)."""
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def _json_lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'::JSON"
    return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::JSON"


def _first_seen(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Earliest fact-table first_seen per (old) company_id — survivorship."""
    return dict(con.execute(
        "SELECT company_id, CAST(MIN(first_seen_at) AS VARCHAR) FROM silver.jobs "
        "WHERE company_id IS NOT NULL GROUP BY company_id"
    ).fetchall())


def _winner_key(seen: dict[str, str]):
    """Deterministic first-seen survivorship: earliest created_at, then the
    lowest SHA-1 of the normalized key."""
    def key(row: dict):
        return (
            seen.get(row["company_id"], "9999-99-99"),
            hashlib.sha1(norm(row["name"]).encode("utf-8")).hexdigest(),
        )
    return key


def _coalesce_notes(raw) -> list[str]:
    notes = raw
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except (ValueError, TypeError):
            notes = [notes] if notes else []
    return [n for n in (notes or []) if n]


def dedup_dim_company(con: duckdb.DuckDBPyConnection) -> dict:
    """Dedup ``silver.dim_company`` in place to the golden-record grain.

    Idempotent: once every row carries the ``dedup_version`` marker, re-runs
    only sweep any still-un-rekeyed fact rows (always-safe).

    Steps (plan migration 3-5):
    1. Cluster all dim names (exact -> stem -> fuzzy-auto ladder).
    2. Survivor per cluster: earliest fact first_seen, tie-break lowest
       SHA-1 of the normalized key.
    3. Rebuild golden rows: winner's enrichment fields (deterministic
       survivorship), ``news_notes`` concatenated (deduped across the group),
       ``news_sentiment`` re-aggregated per the DECIDED rule,
       ``dedup_version`` marker written.
    4. Alias registry: every member name -> golden id (``source='seed'``,
       append-only).
    5. ``silver.jobs.company_id`` re-key: old per-board id -> golden id via
       the cluster mapping (idempotent; re-runs match nothing). Old ids stay
       recoverable by recomputation (SHA-1 of "board|name").
    """
    ensure_company_alias(con)
    existing = {
        r[1] for r in con.execute("PRAGMA table_info('silver.dim_company')").fetchall()
    }
    if "dedup_version" not in existing:
        con.execute("ALTER TABLE silver.dim_company ADD COLUMN dedup_version VARCHAR")

    cols = _DIM_COLS + ["dedup_version"]
    rows = con.execute(
        f"SELECT {', '.join(cols)} FROM silver.dim_company"
    ).fetchall()
    if not rows:
        return {"clusters": 0, "merged": 0, "rekeyed": 0, "aliases": 0, "already": True}
    dim = [dict(zip(cols, r)) for r in rows]
    by_old_id = {r["company_id"]: r for r in dim}

    already = all(r["dedup_version"] for r in dim)
    if already:
        reg = load_alias_registry(con)
        rekeyed = _rekey_jobs(con, reg, by_old_id)
        return {"clusters": len(dim), "merged": 0, "rekeyed": rekeyed,
                "aliases": len(reg), "already": True}

    names = [r["name"] for r in dim]
    clusters = clusters_for_names(names)
    seen = _first_seen(con)
    wkey = _winner_key(seen)

    # name -> (golden name, golden id)
    golden_name_of: dict[str, str] = {}
    for members in clusters:
        win = min((r for r in dim if r["name"] in members), key=wkey)
        gid = golden_company_id(win["name"])
        for m in members:
            golden_name_of[m] = win["name"]

    alias_count = 0
    for r in dim:
        gname = golden_name_of[r["name"]]
        if register_alias(con, r["name"], golden_company_id(gname), "seed"):
            alias_count += 1

    old_to_golden = {
        r["company_id"]: golden_company_id(golden_name_of[r["name"]]) for r in dim
    }
    rekeyed = _rekey_jobs(con, old_to_golden, by_old_id)

    golden: dict[str, list[dict]] = defaultdict(list)
    for r in dim:
        golden[golden_name_of[r["name"]]].append(r)

    values_sql = []
    for gname, members in golden.items():
        gid = golden_company_id(gname)
        win = min(members, key=wkey)
        notes: list[str] = []
        for m in members:
            for n in _coalesce_notes(m["news_notes"]):
                if n not in notes:
                    notes.append(n)
        sent = aggregate_sentiment(m["news_sentiment"] for m in members)
        vals = [
            _lit(gid), _lit(gname), _lit(win["display_name"]),
            _lit(win["source_board"]), _json_lit(win["industry"]),
            _lit(win["size_employees"]), _lit(win["year_founded"]),
            _lit(win["hq_country"]), _lit(win["org_type"]), _lit(win["company_type"]),
            _lit(win["stock_symbol"]), _lit(win["stock_exchange"]),
            _lit(win["latest_funding_type"]),
            _lit(win["latest_funding_amount_usd"]), _lit(win["homepage_url"]),
            _lit(win["enriched_at"]), _lit(win["enrichment_version"]),
            _json_lit(notes), _lit(sent), _lit(win["news_checked_at"]),
            _lit(win["insee_employee_range"]), _lit(win["insee_legal_type"]),
            _lit(win["insee_checked_at"]), _lit(DEDUP_VERSION),
        ]
        values_sql.append("(" + ", ".join(vals) + ")")

    insert_cols = _DIM_COLS + ["dedup_version"]
    con.execute("CREATE TEMP TABLE _golden_company AS "
                f"SELECT * FROM silver.dim_company WHERE FALSE")
    con.execute(
        f"INSERT INTO _golden_company ({', '.join(chr(34) + c + chr(34) for c in insert_cols)}) "
        f"VALUES {', '.join(values_sql)}"
    )
    con.execute("DELETE FROM silver.dim_company")
    collist = ", ".join(f'"{c}"' for c in insert_cols)
    con.execute(f"INSERT INTO silver.dim_company ({collist}) "
                f"SELECT {collist} FROM _golden_company")
    con.execute("DROP TABLE _golden_company")

    return {
        "clusters": len(golden),
        "merged": sum(1 for m in golden.values() if len(m) > 1),
        "rekeyed": rekeyed,
        "aliases": alias_count,
        "already": False,
    }


def _rekey_jobs(
    con: duckdb.DuckDBPyConnection,
    mapping: dict[str, str],
    by_old_id: dict[str, dict] | None = None,
) -> int:
    """Re-key ``silver.jobs.company_id`` old per-board id -> golden id.

    Idempotent: rows already carrying a golden id match nothing (golden ids
    never appear as keys in ``mapping`` after the first pass). Old ids remain
    recoverable by recomputation (SHA-1 of "board|name").
    """
    moved = 0
    for old, golden in mapping.items():
        if old == golden:
            continue
        n = con.execute(
            f"SELECT COUNT(*) FROM silver.jobs WHERE company_id = '{old}'"
        ).fetchone()[0]
        if n:
            con.execute(
                f"UPDATE silver.jobs SET company_id = '{golden}' "
                f"WHERE company_id = '{old}'"
            )
            moved += n
    return moved


# ---------------------------------------------------------------------------
# Incremental resolution (NEW names only) + human review queue
# ---------------------------------------------------------------------------

def resolve_new_names(con: duckdb.DuckDBPyConnection) -> dict:
    """Resolve fact-table company names the registry does not know yet.

    NEW names only — every name already in ``company_alias`` short-circuits
    (never re-resolved). Exact/stem/fuzzy-auto hits re-key the fact rows to
    the golden id and register the alias; misses self-seed their own golden
    row + alias (``source='seed'``) and queue review candidates.
    """
    ensure_company_alias(con)
    stats = {"resolved": 0, "self_seeded": 0, "rekeyed": 0, "review": 0}
    reg = load_alias_registry(con)
    names = [
        r for r in con.execute(
            "SELECT DISTINCT company, source_board FROM silver.jobs "
            "WHERE company IS NOT NULL AND TRIM(company) <> ''"
        ).fetchall()
    ]
    for raw, _board in names:
        n = norm(raw)
        if not n or n in reg:
            continue
        gid, source, conf, _partner = resolve_name(con, raw)
        if gid is None:
            # Self-seed: its own golden row keyed by the normalized name.
            gid = golden_company_id(n)
            stats["self_seeded"] += 1
        if source == "review":
            stats["review"] += 1
        else:
            stats["resolved"] += 1
        if register_alias(con, n, gid, source if source != "review" else "seed",
                          conf):
            # Ensure a golden dim row exists for a freshly self-seeded name.
            con.execute(
                f"INSERT INTO silver.dim_company (company_id, name, display_name, "
                f"source_board, dedup_version) VALUES ('{gid}', '{n}', "
                f"'{raw.replace(chr(39), chr(39) * 2)}', "
                f"'{_board.replace(chr(39), chr(39) * 2)}', '{DEDUP_VERSION}') "
                f"ON CONFLICT (company_id) DO NOTHING"
            )
        moved = con.execute(
            f"SELECT COUNT(*) FROM silver.jobs WHERE company IS NOT NULL "
            f"AND company <> '' AND company_id <> '{gid}' AND "
            f"LOWER(TRIM(REGEXP_REPLACE(company, '\\s+', ' ', 'g'))) = '{n.replace(chr(39), chr(39) * 2)}'"
        ).fetchone()[0]
        if moved:
            con.execute(
                f"UPDATE silver.jobs SET company_id = '{gid}' WHERE company IS NOT NULL "
                f"AND company <> '' AND company_id <> '{gid}' AND "
                f"LOWER(TRIM(REGEXP_REPLACE(company, '\\s+', ' ', 'g'))) = '{n.replace(chr(39), chr(39) * 2)}'"
            )
            stats["rekeyed"] += moved
        reg[n] = gid
    return stats

def write_review_queue(
    con: duckdb.DuckDBPyConnection,
    out_path: str = REVIEW_CSV,
) -> int:
    """Materialize the human-review queue export.

    The file is REWRITTEN (not appended) each run so it is always a complete,
    reviewable artifact: the header row is always present, decided history
    (rejections etc.) is preserved above the current pending pairs, and
    duplicate history lines from earlier appends are collapsed. A pair is
    never re-offered when it is already decided in the CSV or when both names
    already share a golden id in the registry. The named spot-check pairs
    (SPOT_CHECK_PAIRS) are always surfaced for a human decision, whatever the
    score classifier says. Returns the number of pending pairs.
    """
    import csv
    from pathlib import Path

    header = ["name_a", "name_b", "score", "reason", "status"]
    reg = load_alias_registry(con)
    history: list[list] = []
    decided: set[tuple[str, str]] = set()
    p = Path(out_path)
    if p.exists():
        with open(p, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if rows and rows[0] != header:
            rows.insert(0, header)  # legacy headerless file: self-heal
        for row in rows[1:]:
            if len(row) != 5 or row[4] == "pending":
                continue
            key = tuple(sorted((row[0], row[1])))
            if key in decided:
                continue  # collapse repeated rejection lines
            decided.add(key)
            history.append(row)
    pending: list[list] = []
    names = sorted(reg)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if reg[a] == reg[b]:
                continue  # already merged to one golden row
            if tuple(sorted((a, b))) in decided:
                continue
            res = review_reason(a, b)
            if res:
                pending.append([a, b, round(res[0], 1), res[1], "pending"])
    for a, b in SPOT_CHECK_PAIRS:
        a, b = norm(a), norm(b)
        if a not in reg or b not in reg or reg[a] == reg[b]:
            continue
        if tuple(sorted((a, b))) in decided:
            continue
        if any({r[0], r[1]} == {a, b} for r in pending):
            continue
        res = review_reason(a, b)
        pending.append([a, b, round(res[0], 1) if res else "",
                        res[1] if res else "spot-check", "pending"])
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in history + pending:
            w.writerow(row)
    return len(pending)


def apply_review_decision(
    con: duckdb.DuckDBPyConnection,
    name_a: str,
    name_b: str,
    merge: bool,
) -> dict:
    """Apply one human review decision (append-only).

    merge=True: both names point at ONE golden row — the surviving golden row
    (first-seen survivorship) absorbs the other's enrichment (notes
    concatenated, sentiment re-aggregated per the DECIDED rule) and every
    fact row keyed to the absorbed id is re-keyed. merge=False: record the
    rejection in the review CSV as ``rejected`` — never re-offered, never
    merged. The write is deduped: a pair already recorded in the CSV under
    any status is never appended twice.
    """
    reg = load_alias_registry(con)
    a, b = norm(name_a), norm(name_b)
    if a not in reg or b not in reg:
        return {"merged": False, "error": "unknown alias"}
    ga, gb = reg[a], reg[b]
    if ga == gb:
        return {"merged": False, "error": "already same golden row"}
    if not merge:
        import csv
        from pathlib import Path
        p = Path(REVIEW_CSV)
        key = {a, b}
        already = False
        if p.exists():
            with open(p, newline="", encoding="utf-8") as f:
                for row in csv.reader(f):
                    if len(row) == 5 and {row[0], row[1]} == key:
                        already = True
                        break
        if not already:
            with open(p, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([a, b, "", "manual", "rejected"])
        return {"merged": False, "rejected": True}

    rows = con.execute(
        f"SELECT {', '.join(_DIM_COLS)} FROM silver.dim_company "
        f"WHERE company_id IN ('{ga}', '{gb}')"
    ).fetchall()
    dim = [dict(zip(_DIM_COLS, r)) for r in rows]
    if len(dim) != 2:
        return {"merged": False, "error": "golden rows missing"}
    seen = _first_seen(con)
    win, lose = sorted(dim, key=_winner_key(seen))
    keep, drop = win["company_id"], lose["company_id"]
    notes = _coalesce_notes(win["news_notes"])
    for n in _coalesce_notes(lose["news_notes"]):
        if n not in notes:
            notes.append(n)
    sent = aggregate_sentiment([win["news_sentiment"], lose["news_sentiment"]])
    con.execute(
        f"UPDATE silver.dim_company SET "
        f"news_notes = {_json_lit(notes)}, news_sentiment = {_lit(sent)} "
        f"WHERE company_id = '{keep}'"
    )
    _rekey_jobs(con, {drop: keep})
    # Repoint EVERY alias of the absorbed golden row (append-only registry:
    # existing alias rows must be updated in place to keep the registry
    # consistent; the losing row disappears).
    con.execute(
        f"UPDATE silver.company_alias SET company_id = '{keep}' "
        f"WHERE company_id = '{drop}'"
    )
    for name in (a, b):
        register_alias(con, name, keep, "manual")
    con.execute(f"DELETE FROM silver.dim_company WHERE company_id = '{drop}'")
    # Refresh the dedup marker on the surviving row (its derivation changed).
    con.execute(
        f"UPDATE silver.dim_company SET dedup_version = '{DEDUP_VERSION}' "
        f"WHERE company_id = '{keep}'"
    )
    return {"merged": True, "kept": keep, "dropped": drop}
