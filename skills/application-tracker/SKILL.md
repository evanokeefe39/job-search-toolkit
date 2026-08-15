---
name: application-tracker
description: Maintain the application funnel in Twenty CRM: record status transitions (never lose history) and compute response-rate statistics benchmarked against industry norms.
---

## Requirements

Python 3.14+ and uv are required. The CRM bridge lives in the sibling repo
`../crm` (install once: `uv --directory ../crm sync`).


# application-tracker

Use this skill whenever a job application's status changes, a new application is
first mentioned, or the human asks for funnel statistics (response rate, pipeline
summary). Twenty is the single source of truth for application state: keep it
current, and never lose history.

## Data model

Application state lives in Twenty (`http://localhost:3000`) as an **Opportunity**
with these fields (managed by the bridge):

| field | meaning |
|---|---|
| `name` | role/title as on the JD |
| `company` (relation) | company as on the JD |
| `source` | where the job was found (hiringcafe / freework / other) |
| `jobUrl` | job posting URL |
| `folder` | `applications/YYYY-MM-DD_<company-slug>_<role-slug>` — the record key |
| `stage` | one value from the status vocabulary below (pipeline stage) |
| `atsScore` | integer 0-100 from the tailor step; empty until a real value exists |
| `appliedDate` | ISO date the application was submitted; empty until `applied` |
| `outcome` | short terminal label (e.g. `rejected`, `offer_accepted`); empty until terminal |
| `notes` | free text: interview dates, human decisions, correction log — never delete prior notes |

The bridge writes to Twenty with `crm-bridge sync --json '<payload>'`. Payload
keys map 1:1 to the table above (`company`, `role`, `source`, `url`, `folder`,
`status`, `ats_score`, `applied_date`, `outcome`, `notes`). Only non-empty keys
are written, so a transition payload preserves every other field.

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

### 1. Find the record

List the funnel to locate the record by folder or role:

```
uv --directory ../crm run crm-bridge stats
```

For a specific record, match on `folder` (the row key). Ask the human for the
folder slug if it is ambiguous.

### 2. First mention — create the opportunity

If no record exists for the folder, create it with status `shortlisted` (unless
the human states otherwise):

```
uv --directory ../crm run crm-bridge sync --json '{"company":"ACME SaaS","role":"Data Engineer","source":"freework","url":"https://example.com/jobs/123","status":"shortlisted","folder":"applications/2026-08-06_acme-saas_data-engineer"}'
```

Only create records for applications that actually exist (a folder was
scaffolded or the human named one) — never pre-create records from ranked lists.

STOP and present to the human: if the folder slug is ambiguous, before creating
anything.

### 3. Update a status

Forward-only transitions:

```
shortlisted → researching → tailoring → ready → applied → interview → offer
```

plus, from any active status (`shortlisted` … `interview`): `→ rejected | withdrawn`.
`offer`, `rejected`, `withdrawn` are terminal — no transitions out.

Send a `sync` payload with only the changed fields (plus `company`/`role`/`folder`
to identify the record). Side rules:

- `status → applied`: include `applied_date` (today) in the same payload.
- After tailoring completes: set `ats_score` from the real score returned by
  Resume-Matcher — never a guessed number.
- `status → interview` or later: append to `notes` `interviewed: YYYY-MM-DD`.
- Terminal `rejected`/`withdrawn`: move `status`, set `outcome` to a short label,
  and append context to `notes` — never delete the record.

Example (ready → applied):

```
uv --directory ../crm run crm-bridge sync --json '{"company":"ACME SaaS","role":"Data Engineer","folder":"applications/2026-08-06_acme-saas_data-engineer","status":"applied","applied_date":"2026-08-10"}'
```

Verify after every write: re-run the `sync` (idempotent) or `stats` and confirm
exactly the intended fields changed.

### 4. Corrections and history

History is never rewritten. A wrong value is fixed only with human approval: send
the corrected field via `sync` and append an explanatory note (`corrected <field>
from X to Y on YYYY-MM-DD — reason`). Records are never deleted or merged; a
duplicate is resolved by the human, not by deleting one.

STOP and present to the human: any transition that is not forward, any status
value outside the vocabulary, any duplicate `folder`, or any request to delete a
record.

### 5. Stats on request

When the human asks for stats, run:

```
uv --directory ../crm run crm-bridge stats
```

Report:

- applications: count of `applied`/`interview`/`offer`/`rejected`/`withdrawn`
- interviews: count
- response rate: `interviews / applications` — benchmark against the 10-15%
  range; flag deviations and add a small-sample caveat if `applications < 10`
- open items: `shortlisted`/`researching`/`tailoring`/`ready`, grouped by status,
  oldest first; plus "awaiting response" = `applied` with no interview noted.

STOP and present to the human: the full stats summary — the skill reports, it
does not decide.

### 6. Concurrency

Other skills may write Twenty concurrently (e.g. the tailor step sets `ats_score`
while you work). `crm-bridge sync` is an idempotent upsert keyed on `folder`; a
concurrent write only affects the fields it sends, so re-read `stats` immediately
before any transition and re-apply on fresh state.

## Failure handling

If anything unexpected happens — bridge command missing, auth failure, malformed
payload, a status you cannot reconcile, conflicting writes — stop and report to
the human with what you saw. Do not improvise infrastructure: no side trackers
(spreadsheets, notes files), no direct database edits. Report, do not invent.

## Do not

- Never fabricate statuses, `ats_score`, `applied_date`, or dates in `notes`.
- Never transition a record other than the one assigned; bulk changes only on
  explicit human instruction.
- Never delete records or rewrite history; endings go through
  `rejected`/`withdrawn` plus `outcome`/`notes`.
- Never commit personal data; the CRM bridge and its `.env` live in the private
  `crm` repo — never reference the API key in any public repo.
- Never auto-advance a status from hearsay: a transition needs evidence (a human
  decision, a scheduled interview, a completed submission).
