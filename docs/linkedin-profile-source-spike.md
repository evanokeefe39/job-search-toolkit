# LinkedIn profile scraping sources — spike report (2026-08-25)

Goal: identify the most reliable/robust source for scraping LinkedIn recruiter
profiles (Apify or otherwise) and weigh cost vs benefit. Trigger: the
posts→jobs work found direct-HTTP profile fetch is blocked (HTTP 999); assess
whether a dedicated profile source is worth adopting.

## Sources researched

| Source | How it works | Profile cost | Runnability (current setup) | Returns |
|---|---|---|---|---|
| `data-slayer/linkedin-profile-scraper` (Apify) | Bulk public-profile scrape from `linkedin_urls`, no cookies/login | **$4.00 / 1,000 profiles** (~$0.004 ea) | **RUNNABLE — VERIFIED** (account ID `mSiesbz781McL0tYy`) | name, **headline, location, country**, job title, company, experience, education, skills |
| `alwaysprimedev/linkedin-profile-scraper` (Apify) | Same class (`profileUrls`) | $3.50 / 1,000 profiles | RUNNABLE (account ID `24XolEBv54jcBUXpi`) | name, headline, location, current role/company, experience, education, posts |
| `simpleapi/linkedin-profile-scraper` (Apify) | Same class | $19.99/mo + usage | unknown (not added) | similar |
| Proxycurl | Dedicated LinkedIn profile API | ~$0.01–0.06/profile (historical) | **DEAD — shut down July 4, 2025** | n/a |
| Direct HTTP (`fetch_page` on `/in/<slug>/`) | Current approach | $0 | **Blocked — HTTP 999** (anti-bot) | n/a |

## Key finding: the 404 was an account-ownership issue, not unavailability

The first pass returned **HTTP 404** for community actors via
`/v2/acts/{username}/{slug}`. That was because the actors were NOT yet in the
user's Apify account — the slug-based API only resolves **account-owned**
actors. Once added (the console run adds them), they resolve and run. Verified:
`data-slayer/linkedin-profile-scraper` (account ID `mSiesbz781McL0tYy`) ran
**4/4 recruiter profiles SUCCEEDED** in ~35s, returning structured
name/headline/job/company/**location/country** (e.g. Thomas Tarleton — Practice
Lead @ Huxley — New York City, US; Balázs Szántó — Accenture — Hungary).
Pay-per-event: ~$0.004/profile. Run via the `apify-client` SDK
(`client.actor(...).start()` / `.call()`), not hand-rolled REST.

## Cost vs benefit (revised — source IS available)

**Use case A — recruiter-region inference (posts→jobs).** Now feasible and
cheap: scraping the ~26 `queue`-post recruiters ≈ 26 × $0.004 = **~$0.10**, and
the actor returns `location`/`country`/`headline` directly, revealing the
recruiter's region (e.g. a NYC/Huxley recruiter manages US; an Accenture/Hungary
recruiter manages EMEA). BUT value remains marginal for the France pipeline —
text-regex already covers the France-relevant cases, and non-France regions
aren't France leads. The main value is knowing a recruiter's region for
*outreach targeting*.

**Use case B — cold-outreach contacts.** Clear, direct value: the `cold-outreach`
skill needs recruiter contacts (name, title, company, location). $0.004/contact
is trivial and the actor returns exactly those fields. This is the strongest
justification for adopting a profile source.

**Cost is negligible; access is now resolved.** At ~$0.004/profile, even
hundreds of profiles are <$1. The only real questions are (a) which actor
(`data-slayer` $4/1k vs `alwaysprimedev` $3.50/1k — both verified in-account)
and (b) whether the current work needs profile enrichment at all.

## Recommendation

**A reliable profile source exists and is affordable** — `data-slayer/`
`linkedin-profile-scraper` (verified 4/4, $4/1k) or `alwaysprimedev` ($3.50/1k,
5.0★). Both are in the account and runnable. **Use it when** profile enrichment
adds value:
1. **Region inference for queue posts** (~$0.10/batch) if you want recruiter
   regions beyond what post-text gives — low value for the France pipeline
   itself, useful for outreach targeting.
2. **Cold-outreach contacts** — the clear high-value consumer (recruiter
   name/title/company/location for outreach messages).

Recommended pick: `data-slayer/linkedin-profile-scraper` (verified this session,
"Fresh, No Cookies", structured location/country). Note its input key is
`linkedin_urls` (not `profileUrls`).

## Caveats

- All sources are ToS-fragile (LinkedIn restricts profile scraping); the actor
  is public-data-only and compliant by design.
- The actor enriches KNOWN profile URLs — it does not search/discover people.
- Profiles are only useful if public; private profiles return nothing (but cost
  nothing — pay-per-result).
