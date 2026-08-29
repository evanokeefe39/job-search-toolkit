---
name: follow-up
description: Draft (never send) polite follow-up nudges for job applications that have been sitting in `applied` past the 10-day threshold with no reply — present the due queue to the human, draft copy using only claims already in the submitted materials, the human sends it manually, then record the sent follow-up via the CLI (capped at 2 sent per application).
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

## 4. The human sends first

The HUMAN sends the follow-up manually through their own channel (email,
LinkedIn, ATS portal). Never send, never auto-send — the tool has no send
path at all.

## 5. Record the sent follow-up

After the human has actually sent it, record the follow-up so the cap counts
follow-ups **sent** (the plan caps at two *sent* per application):

```
job-search-toolkit application followup-draft --folder '<folder>' --note '<draft text>'
```

This appends `{ts, note}` to the folder's `status.yaml` under `followups`
(the same source `followups-due` reads, so the cap and the due-query cannot
drift). It does not touch the tracker's event feed and does not send
anything. After 2 recorded sent follow-ups the tool refuses a third (cap) —
do not work around it, and do not record a follow-up that was drafted but not
sent.

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
