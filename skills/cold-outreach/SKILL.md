---
name: cold-outreach
description: Prepare cold outreach messages for target companies. Find potential contacts (data team members, hiring managers, recruiters at known agencies), draft personalized LinkedIn messages or emails, and maintain an outreach tracker. Input is a company/role from an application folder or a target company list. Use when preparing to reach out before or after applying, building a contact list, or drafting follow-up messages.
---
## Requirements

Python 3.14+ and uv (package manager) are required. Install the toolkit with:
`pip install job-search-toolkit` (or `uv tool install job-search-toolkit`).


# Cold Outreach — find contacts, draft messages, track

Goal: for a target company (from an application folder or prospect list),
find relevant contacts, draft personalized outreach messages, and track
outreach in a CSV.

## Pre-flight

1. Read `job_search_preferences.yaml` — extract `outreach.targets`,
   `outreach.recruiter_agencies`, `outreach.platforms`, `language`,
   `location.primary`, and `roles.primary`.
2. Input is either:
   - An application folder: `applications/YYYY-MM-DD_<company>_<role>/` —
     read `jd.md` and `research.md` for context
   - A company name + role — treat as a prospect (no application folder yet)

## Playbook

### 1. Find contacts

Search for people at the target company who match the outreach targets:

**Data team members:**
- `web_search: "<company> data engineer linkedin"`
- `web_search: "<company> data platform linkedin"`
- Look for: Data Engineers, Data Platform Engineers, Analytics Engineers,
  Data Architects, Heads of Data, CTOs at smaller companies
- Priority: someone in a similar role to what you're targeting, 1-2 levels
  above (can speak to the work and team culture)

**Hiring managers:**
- `web_search: "<company> <role> hiring manager linkedin"`
- `web_search: "<company> engineering manager linkedin"`
- Look for: Engineering Managers, Data Team Leads, Directors of Engineering,
  anyone listed as the contact on the job posting
- Priority: the person who would directly manage the role

**Recruiters:**
- `web_search: "<agency> recruiter paris tech linkedin"` for known agencies
  in `job_search_preferences.yaml`
- `web_search: "tech recruiter paris data linkedin"`
- `web_search: "<region> data recruitment agency"` to discover new agencies
  (add them to `job_search_preferences.yaml`)
- Look for: internal recruiters at target companies, external recruiters at
  agencies known to place data roles

**Output:** For each contact found, record in `data/outreach_tracker.csv`:
- `date_found`, `company`, `name`, `title`, `linkedin_url`, `contact_type`
  (data_team/hiring_manager/recruiter), `agency` (if recruiter), `notes`

If fewer than 3 contacts found: STOP and report — insufficient contacts
for meaningful outreach.

### 2. Qualify contacts

For each contact, check fit against preferences:

- **Language:** if `language.dealbreakers` includes `hard_french_requirement`
  and the contact's LinkedIn profile is entirely in French with no English
  content, note "possible language barrier" — don't skip, but draft in
  English with a French greeting
- **Location:** prioritize contacts in `location.primary` (Paris) or the
  role's listed location
- **Role match:** prioritize contacts whose title/function aligns with
  `roles.primary`

Sort contacts by priority (highest first):
1. Hiring manager for the specific role
2. Data team member at target company (similar role)
3. Internal recruiter at target company
4. External recruiter at known agency

### 3. Draft outreach messages

For each qualified contact, draft ONE message following these rules:

**Format:**
- LinkedIn message or email (indicate which platform)
- Subject line (email only): short, specific, no clickbait
- Body: 3-4 sentences max
- Never use AI-generated templates that sound generic — each message
  must reference something specific about the company, role, or contact

**Structure:**
1. **Context** — how you found them (LinkedIn, company page, JD)
2. **Relevance** — one sentence connecting your background to their work
3. **Ask** — clear, low-commitment (15-min chat, question about the team,
   not "can you get me a job")

**Example (data team member):**
> Hi [Name] — I came across your work on [specific project/team] at [Company].
> I'm a data engineer with 6 years in Fabric/PySpark/data platform work,
> currently relocating to Paris. Would you be open to a 15-min chat about
> the data engineering landscape there? No ask beyond your perspective.

**Example (hiring manager):**
> Hi [Name] — I saw the [Role] opening at [Company] and your team's work on
> [specific thing]. I've spent the last few years building Fabric data platforms
> and am relocating to Paris. I'd love to learn more about the team's direction —
> would 15 minutes work sometime next week?

**Example (recruiter):**
> Hi [Name] — I'm a data engineer (6 yrs, Fabric/Azure/Python) relocating to Paris
> and looking for contract or permanent roles. Your agency came up in my research
> for Paris tech recruitment. Are you currently working with any companies hiring
> for data platform or analytics engineering roles?

**Anti-patterns (NEVER do these):**
- "I hope this message finds you well"
- "I'm reaching out because I'm passionate about..."
- Generic skill dumps ("I know Python, SQL, Spark, Airflow, dbt...")
- "I believe I would be a great fit for your organization"
- Messages longer than 5 sentences
- Messages that don't reference anything specific to the company or contact

### 4. Human review gate

STOP and present to the human:
- List of contacts found (name, title, company, link)
- Each drafted message
- Ask: "Review and approve each message. I can adjust tone, content, or
  skip any contact."

Do not send any messages. This skill only prepares drafts.

### 5. Track

After human approval, update `data/outreach_tracker.csv`:
- Set `status` to `draft_approved` for each approved message
- Record the `date_approved` and the message text in `notes`
- The human sends messages manually — this skill does not automate sending

### 6. Follow-up (future session)

When re-running this skill for the same company, check `data/outreach_tracker.csv`
for existing entries:
- `draft_approved` → ask human: "Did you send this? Update status?"
- `sent` and >7 days since `date_sent` → offer to draft a follow-up
- `replied` → ask about outcome, update tracker

## Outreach tracker schema

`data/outreach_tracker.csv` (gitignored via `data/outreach_*`):

```
date_found,company,name,title,linkedin_url,contact_type,agency,status,date_approved,date_sent,date_replied,outcome,notes
```

Status values: `found`, `draft_approved`, `sent`, `replied`, `no_response`, `declined`, `connected`

## Do not

- Never send messages — this skill only prepares drafts
- Never fabricate contact information — every contact must be verified via
  web_search or LinkedIn
- Never use generic AI templates — each message must reference company-specific
  details from research.md or web_search
- Never write personal data outside gitignored paths
- Never scrape LinkedIn — use web_search and public profiles only
