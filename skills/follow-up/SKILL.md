---
name: follow-up
description: Draft (never send) polite follow-up nudges for job applications that have been sitting in `applied` past the 10-day threshold with no reply — present the due queue to the human, draft copy using only claims already in the submitted materials, record the draft via the CLI, and stop for human approval before any send.
---

# follow-up

Use this skill when the human asks for follow-ups, when a periodic follow-up
sweep is requested, or when an application has had no response for a while.

The tool never sends anything. Follow-ups are **drafts only** — a note is
appended to the application folder's `status.yaml` (capped at 2 drafts per
application), and the **human** sends the message manually through their own
channel (email, LinkedIn, ATS portal).

## 1. Get the queue

```
job-search-toolkit application followups-due [--days 10]
```

This lists applications whose current tracker stage is exactly `applied`,
whose latest `applied` event is more than 10 calendar days old, and which
have fewer than 2 recorded follow-up drafts, oldest first. Each row shows
the folder, company, role, days since applying, and follow-up count.

## 2. Present the queue to the human

Show the queue and STOP. Let the human pick which applications to nudge.
Never draft for the whole queue unprompted.

## 3. Draft from real material only

For a chosen application, open its folder
(`applications/YYYY-MM-DD_<company-slug>_<role-slug>/`) and base the draft
**only** on claims already in the submitted materials — `jd.md`,
`cv_tailored.yaml`, and the existing `status.yaml` notes in that folder.

- Reference the actual role, company, and date of submission.
- Reuse concrete claims the CV already makes (never invent new ones, never
  exaggerate, never add facts that were not in the submission).
- Keep the tone short, polite, and human: one to three sentences plus the
  note text that will go into the record.

STOP and show the draft to the human for approval before recording it.

## 4. Record the approved draft

```
job-search-toolkit application followup-draft --folder '<folder>' --note '<draft text>'
```

This appends `{ts, note}` to the folder's `status.yaml` under `followups`.
It does not touch the tracker's event feed and does not send anything. After
2 drafts the tool refuses further drafts (cap) — do not work around it.

## 5. The human sends

The HUMAN sends the follow-up manually. Never send, never auto-send, and
never claim a follow-up was sent — only that it was drafted and recorded.

## Failure handling

- A corrupt or unreadable `status.yaml` fails loudly — report it to the
  human; never overwrite or "repair" the file silently.
- If a folder has no matching tracker record, stop and report the mismatch.

## Do not

- Never send a follow-up or trigger any send path.
- Never fabricate claims, dates, or project details not present in the
  submitted materials.
- Never exceed the 2-draft cap per application.
- Never record a follow-up as a tracker stage transition — the tracker
  vocabulary has no "followed up" stage; drafts live in `status.yaml` only.
