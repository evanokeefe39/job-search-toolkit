---
name: record-outcome
description: Record a status transition on an application — writes the append-only status.yaml history inside the application folder AND the shared tracker event via `job-search-toolkit application record`. Use whenever an application's stage changes (shortlisted → researching → tailoring → ready → applied → interview → offer, or rejected/withdrawn), always human-gated.
---

# record-outcome

Record one stage transition on an existing application. The command writes
TWO places in one shot:

1. `status.yaml` inside the application folder
   (`applications/YYYY-MM-DD_<company-slug>_<role-slug>/status.yaml`) —
   append-only transition history + follow-up drafts.
2. The shared tracker event feed, keyed on the folder name
   (`applications/YYYY-MM-DD_<company>_<role>`, e.g.
   `2026-08-07_upclear_power-bi-senior-developer`) as the `job_id`.

## Requirements

Python 3.14+ and uv. The `job-search-toolkit` CLI must be on PATH
(`pip install job-search-toolkit` or `uv tool install job-search-toolkit`).

## Stage vocabulary (fixed — never invent a stage)

`discovered, shortlisted, researching, tailoring, ready, applied, interview,
offer, rejected, withdrawn, ghosted`. Unknown stages are rejected with
ValueError.

## Playbook

### 1. Check the current stage

Read the application's current state before proposing anything:

```
job-search-toolkit tracker current --job 'applications/YYYY-MM-DD_company-slug_role-slug'
```

and/or read `applications/YYYY-MM-DD_company-slug_role-slug/status.yaml`
(`current_stage` + `transitions`). If the folder does not exist, STOP — run
the new-application skill first; never record outcomes for folders that were
never scaffolded.

### 2. Present the proposed transition (human-gated)

STOP and present to the human:

- folder: `applications/YYYY-MM-DD_<company>_<role>`
- current stage → proposed stage
- the evidence for the transition (a human decision, a scheduled interview,
  a completed submission — never hearsay)
- the note text that will be stored (e.g. `ats_score=<n>`,
  `interviewed: YYYY-MM-DD`, source/URL, outcome context)

Do not record until the human confirms.

### 3. Record

```
job-search-toolkit application record --folder 'applications/YYYY-MM-DD_company-slug_role-slug' --stage '<stage>' --ts '<ISO-8601 timestamp>' --note '<context>'
```

- `--ts` is the timestamp of the event (for `applied`, today's date).
- `--note` is free text; prior notes are never deleted.

Verify after every write: re-run
`job-search-toolkit tracker current --job '<folder>'` and confirm exactly the
intended event is the latest one, and check `status.yaml` gained one
transition with `current_stage` updated.

## Invariants

- **Append-only.** History is never rewritten: a wrong value is corrected by
  recording a NEW transition with an explanatory note (`corrected <field>
  from X to Y on YYYY-MM-DD — reason`). Identical re-records
  (same stage/ts/note) are idempotent — they do not duplicate entries.
- **Fixed vocabulary.** Only the stages listed above; anything else raises
  ValueError.
- **Follow-ups are drafts only.** Follow-up notes live in status.yaml
  (`followups:`), are never sent, and produce no tracker event; the cap is 2
  drafts per application (`add_followup` raises FollowupCapError on a third).
- **Personal data only in gitignored paths.** All application state
  (`applications/`, status.yaml, the tracker DB) is gitignored — never write
  personal data into any committed file. This repo is PUBLIC.
- **Never silently overwrite.** A corrupt status.yaml raises
  CorruptStatusError with a recovery path; report it to the human and stop —
  never delete or rewrite the file to make the error go away.

## Do not

- Never record for a folder that does not exist or that was not assigned.
- Never bulk-record or auto-advance stages; each transition is individually
  human-gated.
- Never send follow-ups: drafts are recorded, sending is a human action.
- Never touch `cli.py`, the tracker package, or the pipeline paths — this
  skill only calls the CLI.
