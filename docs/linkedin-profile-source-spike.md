# LinkedIn profile scraping sources — spike report (2026-08-25)

Goal: identify the most reliable/robust source for scraping LinkedIn recruiter
profiles (Apify or otherwise) and weigh cost vs benefit. Trigger: the
posts→jobs work found direct-HTTP profile fetch is blocked (HTTP 999); assess
whether a dedicated profile source is worth adopting.

## Sources researched

| Source | How it works | Profile cost | Runnability (current setup) | Returns |
|---|---|---|---|---|
| `alwaysprimedev/linkedin-profile-scraper` (Apify) | Public-profile scrape, no cookies/login, bulk `profileUrls` | **$3.50 / 1,000 profiles** (~$0.0035 ea) | **NOT runnable** — API 404 (see below) | name, **headline, location**, summary, current role/company, experience, education, posts, company enrichment |
| `data-slayer/linkedin-profile-scraper` (Apify) | Same class | $4.00 / 1,000 profiles | NOT runnable — API 404 | similar structured profile |
| `simpleapi/linkedin-profile-scraper` (Apify) | Same class | $19.99/mo + usage | NOT runnable — API 404 | similar |
| Proxycurl | Dedicated LinkedIn profile API | ~$0.01–0.06/profile (historical) | **DEAD — shut down July 4, 2025** | n/a |
| Direct HTTP (`fetch_page` on `/in/<slug>/`) | Current approach | $0 | **Blocked — HTTP 999** (anti-bot) for 6/8 profiles | n/a |

## Key finding: the recurring Apify access wall

Every community Apify marketplace actor returns **HTTP 404** on the API
(`POST /v2/acts/{id}/runs`, `GET /v2/acts/{id}`) with the current `APIFY_API_TOKEN`.
Only official `apify~` actors (e.g. `apify~google-search-scraper`) run. This is
the identical pattern seen in the jobs-source spike (`docs/linkedin-source-spike.md`):
the current Apify account/token cannot execute community marketplace actors
until one is purchased/added (or the account/plan changes). So the capable,
cheap profile actors exist but are **not reachable from this account today**.

## Cost vs benefit for our use cases

**Use case A — recruiter-region inference (posts→jobs).** Would need to scrape
the ~26 `queue`-post recruiters' profiles to infer managed region. At ~$0.004
each that's ~$0.10 for the whole batch — trivially cheap *if* a source runs.
BUT: we already decided this path is low-value — text-regex covers the
France-relevant cases (EMEA/Europe/France), and non-France regions aren't
France leads. Marginal benefit is small.

**Use case B — cold-outreach contacts.** The `cold-outreach` skill needs
recruiter contacts (name, title, company, location). Profile data here is
directly useful and the cost per contact is low (~$0.004). This is the case
that would justify a profile source — but it's a separate skill, not the
current LinkedIn-adapter priority.

**The binding constraint is access, not cost.** Even at $0.004/profile the
cost is negligible; the blocker is that no source is runnable without either
(a) purchasing/adding a community Apify actor on the account (then running it),
or (b) an alternative paid provider. Direct HTTP is blocked; Proxycurl is dead.

## Recommendation

**Do NOT add a paid profile-scraping dependency now.** The benefit for the
current work (region inference) is marginal, the source isn't runnable with the
current token anyway, and the real consumer (cold-outreach) is a separate skill
not being worked on. Revisit only when:

1. Cold-outreach becomes active and needs recruiter contacts at scale, AND
2. Apify account access is resolved (purchase `alwaysprimedev/linkedin-profile-scraper`
   or equivalent — the cheapest structured option at $3.50/1k).

If/when that happens, `alwaysprimedev/linkedin-profile-scraper` is the
recommended source (5.0★, no cookies, location/headline/role in structured
output, bulk, failed profiles separated).

## Caveats

- All sources are ToS-fragile (LinkedIn restricts profile scraping); the
  chosen actor is public-data-only and compliant by design.
- Profiles are only useful for outreach/region if the recruiter has a public
  profile and a public location/headline — LinkedIn blocks anonymous reads,
  so a paid source is the only reliable route.
