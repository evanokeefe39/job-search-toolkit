---
name: application-tracker
description: Maintain the application funnel through the job-search-toolkit outcome tracker (backend-agnostic: local SQLite by default, Twenty via config): record status transitions (append-only, never lose history) and compute response-rate statistics benchmarked against industry norms.
---

## Requirements

Python 3.14+ and uv are required. The `job-search-toolkit` CLI must be on
PATH (`pip install job-search-toolkit` or `uv tool install job-search-toolkit`).


# application-tracker

Use this skill whenever a job application's status changes, a new application is
first mentioned, or the human asks for funnel statistics (response rate, pipeline
summary). The tracker's event feed is the single source of truth for application
state: keep it current, and never lose history.

## Data model

Application state lives in the tracker's append-only event feed, written through
the repo's own CLI:

```
job-search-toolkit application record --folder '<folder>' --stage '<stage>' --ts '<ISO-8601 timestamp>' [--note '<text>']
```

The backend is selected by `config.yaml` (`tracker.backend`): `sqlite` (default,
zero-install, `data/tracker.db`) or `twenty` (a config swap only — the skills
never hardcode a backend or call Twenty directly).

| event field | meaning |
|---|---|
| `job` | `applications/YYYY-MM-DD_<company-slug>_<role-slug>` — the record key (the folder slug) |
| `stage` | one value from the stage vocabulary below (pipeline stage) |
| `ts` | ISO-8601 timestamp of the event |
| `note` | free text: source, job URL, ats_score (`ats_score=<n>`), interview dates, human decisions, correction log — never delete prior notes |

Recording is append-only: identical events (same job/stage/ts/note) are
idempotent, so a re-run never duplicates history, and every stage transition is
preserved as a new event.

### Stage vocabulary

| stage | meaning |
|---|---|
| `shortlisted` | picked from the ranked output as a possible application |
| `researching` | research phase (new-application skill) in progress |
| `tailoring` | resume tailoring (tailor-resume skill) in progress |
| `ready` | tailored CV + audit + PDF done, not yet submitted |
| `applied` | application submitted; record the date in `ts` |
| `interview` | interview scheduled or underway; note the date(s) in `note` |
| `offer` | offer received; record details in `note` |
| `rejected` | terminal; record context in `note` |
| `withdrawn` | terminal, withdrawn by us; record reason in `note` |

(The tracker also accepts `discovered` and `ghosted`; this skill does not set
them on its own — only on explicit human instruction.)

## Playbook

### 1. Find the record

List the funnel to locate the record by folder or stage:

```
job-search-toolkit tracker outcomes
```

For a specific record, match on `job` (the folder slug):

```
job-search-toolkit tracker current --job 'applications/YYYY-MM-DD_company-slug_role-slug'
```

Ask the human for the folder slug if it is ambiguous.

### 2. First mention — create the record

If no record exists for the folder, record it at stage `shortlisted` (unless
the human states otherwise):

```
job-search-toolkit application record --folder 'applications/2026-08-06_acme-saas_data-engineer' --stage 'shortlisted' --ts '2026-08-06T12:00:00' --note 'source=freework url=https://example.com/jobs/123 role="Data Engineer" company="ACME SaaS"'
```

Only create records for applications that actually exist (a folder was
scaffolded or the human named one) — never pre-create records from ranked lists.

STOP and present to the human: if the folder slug is ambiguous, before creating
anything.

### 3. Update a stage

Forward-only transitions:

```
shortlisted → researching → tailoring → ready → applied → interview → offer
```

plus, from any active stage (`shortlisted` … `interview`): `→ rejected | withdrawn`.
`offer`, `rejected`, `withdrawn` are terminal — no transitions out.

Record one event per transition. Side rules:

- `stage → applied`: use today's date as `ts`.
- After tailoring completes: record the real ats_score returned by the tailor
  pipeline as `--note 'ats_score=<n>'` — never a guessed number.
- `stage → interview` or later: append to the note `interviewed: YYYY-MM-DD`.
- Terminal `rejected`/`withdrawn`: record the stage and put a short outcome
  label plus context in `note` — never delete history.

Example (ready → applied):

```
job-search-toolkit application record --folder 'applications/2026-08-06_acme-saas_data-engineer' --stage 'applied' --ts '2026-08-10T12:00:00'
```

Verify after every write: re-run `tracker current --job '<folder>'` and confirm
exactly the intended event is the latest one.

### 4. Corrections and history

History is never rewritten. The feed is append-only: a wrong value is fixed
only with human approval, by recording a new event with an explanatory note
(`corrected <field> from X to Y on YYYY-MM-DD — reason`). Records are never
deleted or merged; a duplicate is resolved by the human, not by deleting one.

STOP and present to the human: any transition that is not forward, any stage
value outside the vocabulary, any duplicate folder, or any request to delete a
record.

### 5. Stats on request

When the human asks for stats, run:

```
job-search-toolkit tracker outcomes --json
```

Report:

- applications: count of jobs whose latest stage is `applied`/`interview`/`offer`/`rejected`/`withdrawn`
- interviews: count
- response rate: `interviews / applications` — benchmark against the 10-15%
  range; flag deviations and add a small-sample caveat if `applications < 10`
- open items: latest stage `shortlisted`/`researching`/`tailoring`/`ready`,
  grouped by stage, oldest first; plus "awaiting response" = `applied` with no
  interview noted.

STOP and present to the human: the full stats summary — the skill reports, it
does not decide.

### 6. Concurrency

Other skills may record events concurrently (e.g. the tailor step records
`ats_score`). Recording is append-only and idempotent on identical events, so
concurrent writes never corrupt state — but re-read
`tracker current --job '<folder>'` immediately before any transition and
re-apply on fresh state.

## Failure handling

If anything unexpected happens — tracker command missing, malformed arguments,
an unknown stage, a state you cannot reconcile, conflicting writes — stop and
report to the human with what you saw. Do not improvise infrastructure: no side
trackers (spreadsheets, notes files), no direct database edits. Report, do not
invent.

## Do not

- Never fabricate stages, `ats_score`, dates, or note content.
- Never transition a record other than the one assigned; bulk changes only on
  explicit human instruction.
- Never delete records or rewrite history; endings go through
  `rejected`/`withdrawn` plus notes.
- Never commit personal data; application state lives in gitignored paths
  (`applications/`, the tracker DB) — never write it into any public file.
- Never auto-advance a stage from hearsay: a transition needs evidence (a human
  decision, a scheduled interview, a completed submission).
