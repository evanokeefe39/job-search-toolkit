---
name: application-tracker
description: Maintain the application tracker: record status transitions, never delete rows, and compute response-rate statistics benchmarked against industry norms.
---

# application-tracker

Use this skill whenever a job application's status changes, a new application is
first mentioned, or the human asks for tracker statistics (response rate,
pipeline summary). The tracker is the single source of truth for application
state: keep it current, and never lose history.

## Data model

`tracker.csv` lives at the repo root and is gitignored (`.gitignore` covers
`*.csv`). The repo is PUBLIC — nothing personal may ever be committed; personal
data lives only in gitignored paths (`resume/`, `applications/`, `tracker.csv`).

One header row + one row per application. Columns, in order:

| column | meaning |
|---|---|
| `date_added` | ISO date `YYYY-MM-DD` the application entered the tracker (normally the date in the folder name) |
| `company` | company name as on the JD |
| `role` | role/title as on the JD |
| `source` | where the job was found (e.g. `free-work`) |
| `url` | job posting URL |
| `status` | one value from the status vocabulary below |
| `folder` | `applications/YYYY-MM-DD_<company-slug>_<role-slug>` — the row key |
| `ats_score` | integer 0-100 from the tailor step; empty until a real value exists |
| `applied_date` | ISO date the application was submitted; empty until `applied` |
| `outcome` | short terminal label (e.g. `rejected`, `offer_accepted`); empty until terminal |
| `notes` | free text: interview dates, human decisions, correction log — never delete prior notes |

Slugs: lowercase, hyphens, no accents. The `folder` cell must match the
application folder name exactly and is never edited after creation.

CSV hygiene: if a cell contains a comma (company names, notes), wrap the whole
cell in double quotes. Keep exactly 11 fields per row and the header intact.

### Status vocabulary

| status | meaning |
|---|---|
| `shortlisted` | picked from the ranked output as a possible application |
| `researching` | research phase (new-application skill) in progress |
| `tailoring` | resume tailoring (tailor-resume skill) in progress |
| `ready` | tailored CV + audit + PDF done, not yet submitted |
| `applied` | application submitted; set `applied_date` |
| `interview` | interview scheduled or underway; note the date(s) in `notes` |
| `offer` | offer received; record details in `notes`/`outcome` |
| `rejected` | terminal; record context in `notes`, short label in `outcome` |
| `withdrawn` | terminal, withdrawn by us; record reason in `notes` |

## Playbook

### 1. Find the row

Read the tracker: `read tracker.csv` (a line range works too, e.g.
`read tracker.csv:1-30`). Locate the row by its folder slug:

```
grep pattern="applications/2026-08-06_acme-saas_data-engineer" paths=["tracker.csv"]
```

### 2. First mention — append a new row

If no row exists for the folder, append one with `edit` (`INS.TAIL` on
`tracker.csv`; if the file does not end in a newline, add it first). Row
template:

```
<date_added>,<company>,<role>,<source>,<url>,shortlisted,<folder>,,,,
```

Example:

```
2026-08-06,ACME SaaS,Data Engineer,free-work,https://example.com/jobs/123,shortlisted,applications/2026-08-06_acme-saas_data-engineer,,,,
```

`date_added` = today; `status` = `shortlisted` unless the human states otherwise;
`ats_score`, `applied_date`, `outcome` stay empty. Only create rows for
applications that actually exist (a folder was scaffolded or the human named
one) — never pre-create rows from ranked lists.

STOP and present to the human: if `tracker.csv` does not exist at all, or the
folder slug is ambiguous, before creating anything.

### 3. Update a status

Forward-only transitions:

```
shortlisted → researching → tailoring → ready → applied → interview → offer
```

plus, from any active status (`shortlisted` … `interview`): `→ rejected | withdrawn`.
`offer`, `rejected`, `withdrawn` are terminal — no transitions out.

Use `edit` to change ONLY the `status` cell (and its dependent cells in the same
edit), keeping every other cell byte-identical. Side rules:

- `status → applied`: set `applied_date` to today in the same edit.
- After tailoring completes: set `ats_score` from the real score returned by
  Resume-Matcher — never a guessed number.
- `status → interview` or later: note the date in `notes` as
  `interviewed: YYYY-MM-DD`.
- Terminal `rejected`/`withdrawn`: move `status`, set `outcome` to a short label,
  and append context to `notes` — never delete the row.

Verification after every edit: re-`read` the row, confirm exactly the intended
cells changed, and the line still has 11 comma-separated fields.

### 4. Corrections and history

History is never rewritten. A wrong cell is fixed only with human approval: make
the edit and append an explanatory note (`corrected <field> from X to Y on
YYYY-MM-DD — reason`). Rows are never deleted, merged, or renumbered; a
duplicate row is resolved by the human, not by deleting one.

STOP and present to the human: any transition that is not forward, any status
value outside the vocabulary, any duplicate `folder`, or any request to delete a
row.

### 5. Stats on request

When the human asks for stats, compute them in an eval cell (py) and present the
summary:

```python
import csv
rows = list(csv.DictReader(open("tracker.csv", encoding="utf-8")))
submitted = {"applied", "interview", "offer", "rejected", "withdrawn"}
applications = [r for r in rows if r["status"] in submitted]
interviewed = [r for r in rows if r["status"] in {"interview", "offer"}
               or "interviewed:" in (r["notes"] or "")]
response_rate = 100 * len(interviewed) / len(applications) if applications else 0
```

Report:

- applications: `len(applications)`
- interviews: `len(interviewed)`
- response rate: `response_rate`% — benchmark against the 10-15% range from the
  tailoring literature; flag deviations and add a small-sample caveat if
  `len(applications) < 10`
- open items: rows still in `shortlisted`/`researching`/`tailoring`/`ready`,
  grouped by status, oldest `date_added` first; plus "awaiting response" =
  `applied` rows with no interview noted.

STOP and present to the human: the full stats summary — the skill reports, it
does not decide.

### 6. Concurrency

Other agents may edit `tracker.csv` (e.g. the tailor step updates a row while
you work). Re-`read` the file immediately before any edit; if it changed since
your last read, re-apply on the fresh content.

## Failure handling

If anything unexpected happens — missing file, malformed or unparseable CSV, a
status you cannot reconcile, conflicting edits — stop and report to the human
with what you saw. Do not improvise infrastructure: no new tracker scripts, no
side trackers (spreadsheets, notes files), no schema changes. Report, do not
invent.

## Do not

- Never fabricate statuses, `ats_score`, `applied_date`, or dates in `notes`.
- Never edit rows for other applications than the one assigned; bulk changes
  only on explicit human instruction.
- Never delete rows or rewrite history; endings go through
  `rejected`/`withdrawn` plus `outcome`/`notes`.
- Never commit `tracker.csv` (gitignored), and never write personal data
  outside the gitignored paths (`resume/`, `applications/`, `tracker.csv`) —
  this repo is PUBLIC.
- Never auto-advance a status from hearsay: a transition needs evidence (a
  human decision, a scheduled interview, a completed submission).
