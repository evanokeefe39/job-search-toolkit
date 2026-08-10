# ATS LLM Rules Compendium

Extracted 2026-08-09 from 14+ tools (all source-verified). Designed for LLM
prompt injection as system context. Companion to the machine-parseable
`docs/ats_llm_rules.yaml`.

## Quick Stats

| Metric | Count |
|---|---|
| Total rules | 56 |
| Categories | 8 (format, structure, content, ontology, anti_fabrication, scoring, content_quality, user_policy) |
| Prompt templates | 3 (system, jd_analysis, gap_analysis) |
| Sources consulted | 17 (14 verified, 3 dead ends) |
| Code-verified rules | 50 / 56 |

## Rule Categories

### 1. Format (10 rules — ATSFlow formatting.js, CODE_VERIFIED)

Every rule has exact score deductions and severity levels from the source code.

| ID | Rule | Severity |
|---|---|---|
| F1 | NO tables | critical |
| F2 | NO multi-column layouts | critical |
| F3 | NO headers/footers with content | high |
| F4 | NO images, photos, charts, icons, logos | medium |
| F5 | NO text boxes or floating elements | high |
| F6 | Web-safe fonts only (6 allowed) | low |
| F7 | NO unicode/special bullets | low |
| F8 | Consistent date formats | low |
| F9 | PDF preferred (100) / DOCX acceptable (90) | medium |
| F10 | NO background colors or shading | low |

### 2. Structure (10 rules — ATSFlow structure.js, CODE_VERIFIED)

| ID | Rule | Severity |
|---|---|---|
| S1 | Standard section headers only (16 recognized) | medium |
| S2 | Parseable contact info (name+email+phone REQUIRED) | critical |
| S3 | Reverse chronological order | medium |
| S4 | Spell out acronyms on first use | low |
| S5 | Clear, standard job titles (no jargon) | medium |
| S6 | Proper section ordering (Contact→Summary→Exp→Edu→Skills→...) | low |
| S7 | NO orphaned/empty sections | low |
| S8 | Consistent heading hierarchy (H1=name, H2=section, H3=job) | low |
| S9 | Clear section boundaries with spacing | low |
| S10 | NO complex tables (merged cells, nesting) | high |

### 3. Content (10 rules — ATSFlow content.js, CODE_VERIFIED)

| ID | Rule | Threshold |
|---|---|---|
| C1 | Keyword density 2-8% of total words | <2% or >8% = fail |
| C2 | Dedicated Skills section with ≥5 skills | <5 = fail |
| C3 | Quantified achievements ≥40% of bullets | <40% = fail |
| C4 | ZERO personal pronouns | -10 per pronoun |
| C5 | Action verb bullets ≥80% (22 verbs recognized) | <80% = fail |
| C6 | Appropriate length 400-1400 words **(superseded by UP1)** | SUPERSEDED |
| C7 | NO typos (8 common typos checked) | -10 per issue |
| C8 | Industry keywords ≥40% match rate | <40% = fail |
| C9 | Proper noun capitalization correct | penalty per issue |
| C10 | NO excessive jargon (>5 unrecognized abbreviations) | >5 = fail |

### 4. Ontology-Based Matching (5 rules — amiradridi/Job-Resume-Matching 134★, CODE_VERIFIED)

| ID | Rule |
|---|---|
| O1 | Degree-level matching via ontology (0=none..5=PhD). Candidate ≥2 levels above min → 0.5; 0-1 above → 1.0 |
| O2 | Major matching: exact match → 1.0; same category → 0.5; different → 0 |
| O3 | Skills semantic matching: SentenceTransformer('all-mpnet-base-v2') + cosine ≥0.4 threshold |
| O4 | Final score = mean(skills, degree, major) / 3 |
| O5 | Embedding benchmark: 6 models evaluated (precision@15); bert-base-nli-mean-tokens won |

### 5. Anti-Fabrication (9 rules — 5+ tools, CODE_VERIFIED)

Non-negotiable hard constraints for any LLM tailoring. Every tool agrees.

| ID | Rule | Tools |
|---|---|---|
| A1 | NEVER fabricate facts/numbers/metrics | 5 |
| A2 | NEVER add technologies/skills/platforms | 5 |
| A3 | NEVER change titles/dates/company names | 5 |
| A4 | NEVER add target company as employer | 3 |
| A5 | Same bullet count per role as original | 3 |
| A6 | Unchanged bullets returned exactly | 2 |
| A7 | Minimal edit: variant word only | 2 |
| A8 | No self-assessment clauses | 2 |
| A9 | Frozen sections: education/certs/references | 3 |

### 6. Scoring Weights (5 rules — consensus from 3 tools)

| ID | Dimension | Consensus Weight |
|---|---|---|
| W1 | Keywords/JD match | 50% |
| W2 | Format quality | 20% |
| W3 | Section completeness | 15% |
| W4 | Content quality | 10% |
| W5 | Contact info | 5% |

### 7. Content Quality Thresholds (5 rules — Resume-Analyzer-MLOps 7★, CODE_VERIFIED)

| ID | Rule | Threshold |
|---|---|---|
| Q1 | Word count 200-1000 **(superseded by UP1)** | SUPERSEDED |
| Q2 | ≥5 quantifiable metrics | <5: -20 |
| Q3 | ≥3 action verbs from list | <3: -15 |
| Q4 | Contact scoring: email 40%, phone 30%, LinkedIn 20%, location 10% | — |
| Q5 | Format warnings: >50 special chars, tabs, short line length | — |

### 8. User Policy (2 rules — USER_POLICY, highest authority)

Standing preferences that override all industry-derived rules.

| ID | Rule | Severity |
|---|---|---|
| UP1 | TARGET 1 PAGE, 2 pages absolute max. Rich, tightly tuned to JD. Every bullet earns its space. SUPERSEDES C6 and Q1. | critical |
| UP2 | SABBATICAL: NOT in experience section. Summary mention only. TEMPORARY — re-evaluate when in-flight projects land. | critical |


## What Changed (vs 2026-08-09 v1, updated v2)

| Before | After |
|---|---|
| 35 rules, 10 sources | 56 rules, 17 sources |
| 7 categories | 8 categories (+user_policy) |
| No length policy | UP1: target 1 page, 2 max, overrides C6 and Q1 |
| No sabbatical policy | UP2: summary-only, never in experience |
| Format rules: 8 generic | Format rules: 10 code-verified with exact score deductions |
| Structure rules: 5 generic | Structure rules: 10 code-verified with severity levels |
| Content rules: 8 generic | Content rules: 10 code-verified with numeric thresholds |
| No ontology rules | 5 ontology rules (degree, major, skills matching) |
| Source inventory: shallow rows flagged | All shallow rows completed or dead-end-flagged |

## Key Distinctions

- **Length rules: UP1 overrides all.** C6 (400-1400 words) and Q1 (200-1000) are industry references only. Actual target: rich 1 page, 2 max.
- **400-1400 words** (ATSFlow) vs **200-1000 words** (Resume-Analyzer) — both are informational only; UP1 governs actual length target.
- **≥80% action verb bullets** (ATSFlow, 22 verbs checked) vs **≥3 action verbs total** (Resume-Analyzer) — ATSFlow's check is more rigorous.
- **≥40% quantified bullets** (ATSFlow) vs **≥5 numbers** (Resume-Analyzer) — ATSFlow's ratio-based check is more robust across different resume lengths
- Vendor help docs (Greenhouse, Lever) are dead ends — HTTP 404 and 401 respectively
