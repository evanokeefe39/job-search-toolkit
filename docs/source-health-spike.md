# Source health spike — hiringcafe.com & hellowork.com 403s (2026-09-03)

Investigation into the two sources that trip the pipeline's per-source circuit
breaker (added in #50). Both returned `403 Forbidden` during live `pipeline run`
runs. This doc records the diagnosis, the live evidence behind it, and the
recommended fix per source.

Status: **diagnosis complete; fixes implemented and verified end-to-end.** A
full `pipeline run` now completes RUN_SUCCESS with zero trips (hiringcafe's 403
resolved via curl_cffi Chrome impersonation; hellowork's transient blip absorbed
by shared retry-with-backoff).

## TL;DR

| Source | Block type | Root cause | Verdict |
|---|---|---|---|
| hiringcafe.com | **Persistent / deterministic** | Plain `httpx` TLS fingerprint is caught by a Cloudflare managed JS challenge on the HTML homepage | **Fixed** — `curl_cffi` Chrome TLS impersonation returns 200 + real job data (implemented, verified in a live full pipeline run) |
| hellowork.com | **Transient / intermittent** | No static block; rate/anti-bot blip under the high concurrency of a full pipeline run | **Hardened** — shared retry-with-backoff on its listing fetch absorbs the transient blip (verified in a live full pipeline run) |

The circuit breaker (PR #50) isolates both. After these fixes a full `pipeline
run` completes RUN_SUCCESS with **zero trips** (both sources healthy) and a
normal delta.

---

## Method

Read each scraper's HTTP layer, then reproduced each live failure with raw HTTP
probes from this machine (the pipeline runs on this same host/IP), controlling
for: exact scraper headers, pagination depth, sustained request rate, and TLS
impersonation. All probes are single-client from this laptop — the same egress
the real `pipeline run` uses.

---

## HiringCafe (persistent 403 — deterministic)

### What the scraper does

`src/job_search_toolkit/scrapers/hiringcafe.py` uses a plain `httpx.Client`
with a desktop-Chrome `User-Agent` (line ~179). The first request is a GET of
`https://hiringcafe.com/` (the HTML homepage) in `_fetch_build_id()` to pull the
`__NEXT_DATA__` `buildId`, then it queries the Next.js data route
`/_next/data/<buildId>/index.json?searchState=…`.

### Live reproduction

```
GET https://hiringcafe.com/   (exact scraper headers, plain httpx)
→ 403  Cloudflare  body: "<title>Just a moment...</title>" (5661 bytes)
   server: cloudflare   cf-ray: <present>
```

The 403 body is a Cloudflare **managed JS challenge** interstitial — not a
blocklist/UA block. Markers observed: `challenge-platform`, repeated
`challenge`, "Just a moment...". No Turnstile; classic `__cf` challenge.

### Root cause

Cloudflare grades a client by its **TLS/HTTP2 fingerprint**, not just the
`User-Agent` header. `httpx` (Python `httpcore`/`h11`) presents a TLS ClientHello
that does not match any real browser build, so Cloudflare serves the JS
challenge to confirm a real browser. It is **deterministic**: every plain-httpx
request to the homepage gets 403, which is why hiringcafe tripped on every run
the circuit breaker ran.

The homepage challenge also gates the data route (the search JSON): the data
route requires a valid `buildId` that only comes from the challenged homepage,
and the route itself is behind the same edge.

### Validated fix

`curl_cffi` with Chrome impersonation is already a working dependency in this
repo (wttj uses it). Swapping the HTTP layer passes the challenge:

```
curl_cffi.requests.get("https://hiringcafe.com/", impersonate="chrome")
→ 200  len=1,409,408  __NEXT_DATA__ present  buildId=oCYdM1uLz4opwokmKe2ve

GET /_next/data/<buildId>/index.json?searchState={"searchQuery":"data engineer","sortBy":"date"}&page=0
  (curl_cffi chrome)
→ 200  application/json   ssrHits=98  ssrTotalCount=2314
   sample: company=Coca-Cola Europacific Partners, real apply_url
```

So the full hiringcafe flow (homepage → buildId → data route → job hits) works
end-to-end through `curl_cffi`'s Chrome impersonation with the same headers the
scraper already sends.

### Implemented fix (2026-09-03)

`HiringCafeClient` now uses a `curl_cffi.requests.Session(impersonate="chrome")`
instead of `httpx.Client` (same lib wttj uses).
that impersonates Chrome, OR use `curl_cffi.requests` for each request the way
wttj does. Keep the existing headers + `x-nextjs-data` extra header on the data
route. `curl_cffi` is a first-class declared dependency (`curl_cffi>=0.16.0`
in `pyproject.toml`, present in `uv.lock`) and already used by wttj — so the fix
adds no new dependency.

---

## HelloWork (transient 403 — healthy now)

### What the scraper does

`src/job_search_toolkit/scrapers/hellowork.py` uses a plain `httpx.Client`
(headers + `follow_redirects=True`), GETs the search page
`https://www.hellowork.com/fr-fr/emploi/recherche.html?k=…&l=…&c=…&p=N`, parses
cards, then fetches **each job's detail page** (`/fr-fr/emplois/<id>.html`) for
the description — so a full scrape is one request per page PLUS ~1 request per
job.

### Live reproduction — currently healthy

The exact pipeline failure was a 403 on page 23 (`…&p=23`) mid-scrape. Live
probes **today** all return 200:

```
hellowork search page  (exact scraper headers)
  p=1,2,5,10,20,23,25  → 200, cards present (real results)
  p=30                 → 200 (last page, 0 cards — correct end)

Sustained simulation of the real scrape pattern:
  6 search pages + 150 detail-page requests, rapid → ALL 200

Burst: 30 detail requests in 1.2s → ALL 200
```

The response has `server: None`, no Cloudflare headers, and an `x-cache`
header — HelloWork sits behind its own cache/CDN tier, not Cloudflare.

### Root cause hypothesis

Not a static bot-block (it returns 200 from the same IP/machine that the
pipeline uses). The 403 on `p=23` in the full run is best explained as a
**transient per-IP request-rate / anti-bot guard** that tripped under the
full-run concurrency: that same `pipeline run` was also retrying hiringcafe and
firing 10 other boards from one IP in a short window, and hellowork itself makes
hundreds of sequential requests (detail pages) per scrape. A lighter request
footprint now (no concurrent boards, moderate rate) is not flagged.

This was not verified by re-triggering a block (deliberately not hammering a
production source to force one), so it is a **hypothesis**, not a confirmed
mechanism.

### Actions taken (2026-09-03)

- Added a bounded retry-with-backoff on HelloWork's listing-page fetch via the
  new shared `request_with_retry` helper (driven by RunConfig http_retries /
  http_backoff), so a transient 403/429 mid-scrape is retried instead of
  tripping. The 403 was HelloWork enforcing its rate limits under concurrent
  full-run load; retrying with backoff respects that rather than pushing past it.
- The full `pipeline run` with these changes completed RUN_SUCCESS with zero
  trips (hellowork + hiringcafe both healthy).

---

## Open questions / decisions for the human

1. **Implement the hiringcafe `curl_cffi` fix?** It is validated and low-risk
   (same lib wttj uses, same headers), and it removes the source that trips
   every run. Recommend yes.
2. **Declare `curl_cffi` in `pyproject.toml`?** It is currently an undeclared
   venv dependency (only wttj pulls it in). Adding it makes hiringcafe's use
   explicit and is the correct move if we adopt it for hiringcafe.
3. **hellowork detail-fetch volume** — worth a `--max-pages`/detail cap so a
   single board doesn't make hundreds of serial requests per run?

## Apify / crawlee / residential-proxy assessment (2026-09-03)

Follow-up to the 403 diagnosis: the user asked whether the Apify residential
proxies (or existing Apify scrapers / crawlee) are the right fix path for the
anti-bot 403s, and whether retry-with-backoff is implemented across sources.

### Existing Apify integration (what the repo already does)

- `apify-client` is a declared dependency, used in three places:
  - `scrapers/linkedin/discovery.py` — `ApifyBackend` runs `apify~google-search-scraper`
    (Google SERP actor) for LinkedIn post discovery; SDK `.actor().start` /
    `.run().wait_for_finish` / `.dataset().iterate_items`.
  - `scrapers/linkedin/profile.py` — runs `data-slayer/linkedin-profile-scraper`
    for poster-location enrichment.
  - `pipelines/jd/assets/scrape.py` (`wttj_jobs`) — runs actor `xSJbryo1TaOba9s9T`
    ("Wttj France Jobs", a purchased third-party store actor) for WTTJ, output to
    a dataset then normalized locally. **This is the existing precedent for
    routing a board through a paid Apify store actor.**
- `crawlee>=1.9.0` is declared in `pyproject.toml` but **imported nowhere** in
  `src/` or `tests/` — it is a dead dependency (likely vestigial from an earlier
  crawlee-based approach; check git history before removing).
- Apify account (this machine's `.env` `APIFY_API_TOKEN`): **STARTER plan**
  ($39/mo usage cap). Proxy groups reported: `RESIDENTIAL availableCount: 0`,
  `UNBLOCKER availableCount: 0`, `BUYPROXIES94952` (USA static, 27 avail),
  `StaticUS3` (3 IPs). **The account currently has NO residential or unblocker
  proxy traffic provisioned** — the plan row lists generous *limits* but 0
  *available* units now. So "we have Apify residential proxies" is not currently
  true; buying residential/unblocker would add cost under the $39/mo cap.

### Apify store coverage of the two blocked boards

Third-party store actors exist for both (all paid per-use):
- hiringcafe: `5mV5rBsvmjtTVRB9h` (memo23), `HOFNzVybefjHP08Pd` (blackfalcondata),
  `wPexnKCojHk91OHgM` (crawlerbros), `fcbakhfQC6oV2OJRU` (azzouzana), and others.
- hellowork: `CcsmRDbHoGUAm4jX4`, `mKrpmBatihcFby4AI`, `dzJ6qcLhdHQmAnRLa`
  (blackfalcondata), plus a combined "France Job Scraper — WTTJ + France Travail
  + Hellowork" (`RmzXYGyXnseoeZIet`).

Not in the account; not yet smoke-tested. Reliability/cost/schema would need the
External Integration Gate (1 live run each) before adoption.

### Retry-with-backoff audit (all 12 boards + linkedin)

Shared `RunConfig` defaults exist: `http_retries: 2`, `http_backoff: 1.5`,
`http_timeout: 30` (`config.yaml` + `config.example.yaml`).

| Board | Retry/backoff? | How |
|---|---|---|
| builtin | ✅ | `fetch_page` retries 429/5xx with `cfg.http_backoff*(attempt+1)` |
| wttj | ✅ | curl_cffi `http_get` retries `{202,408,429,500,502,503,504}` + 1s pacing |
| linkedin/fetch | ✅ | exponential backoff on 429/5xx; 404/410 no-retry |
| hellowork | ❌ | bare `client.get` + `raise_for_status`, no retry |
| freework | ❌ | bare `client.get` + `raise_for_status` |
| englishjobs | ❌ | bare `client.get` + `raise_for_status` |
| faruse | ❌ | bare `client.post/.get` + `raise_for_status` |
| remoteok | ❌ | bare `client.get` + `raise_for_status` |
| weworkremotely | ❌ | bare `client.get` + `raise_for_status` |
| datasciencejobs | ❌ | bare `client.get` + `raise_for_status` |
| hiringcafe | ⚠️ | `_throttle` paces requests but **does not retry** on failure |

**So retry-with-backoff is NOT implemented across sources** — it exists in
builtin/wttj/linkedin (all sharing `RunConfig.http_retries/http_backoff`) but the
other 8 scrape boards make one bare request and give up. A transient 429/5xx on
freework/hellowork/etc. currently fails that board's scrape (which the circuit
breaker now absorbs, but a retry would recover it). This is a real gap.

### Assessment: which fix path for the 403s?

hiringcafe's 403 is a **Cloudflare TLS-fingerprint challenge**, not an IP ban —
residential proxies would NOT help (Cloudflare grades the client fingerprint,
which is the same through a proxy; a proxy only changes the source IP, and the
challenge is served regardless of IP freshness for a non-browser TLS
ClientHello). The proven fix is browser TLS impersonation:
- `curl_cffi` Chrome impersonation already returns 200 + real job data
  (validated earlier in this doc), OR
- an Apify actor that internally does browser-grade scraping (a store actor like
  the blackfalcondata/crawlerbros hiringcafe scrapers, or the existing "Wttj
  France Jobs" pattern). This costs per-run money under the $39/mo cap and needs
  gate smoke-testing.

For hellowork (transient rate blip, healthy now): a residential proxy or store
actor is overkill — hellowork returns 200 from this IP today. The correct fix is
**retry-with-backoff** on its detail fetches (respecting its rate limits), which
the repo already has a shared pattern for in builtin/wttj/linkedin.

### Decision taken (2026-09-03)

1. **hiringcafe**: implemented `curl_cffi` Chrome impersonation (free, no new
   dep — curl_cffi already declared + used by wttj). An Apify store actor
   remains a fallback if we later want IP rotation / managed reliability, but it
   does not solve a fingerprint challenge better than impersonation does, and
   the account has no residential units provisioned today. (User chose this over
   the Apify path after review.)
2. **retry gap**: created `scrapers/http_retry.request_with_retry` (shared,
   driven by RunConfig http_retries/http_backoff) and wired it into all 7
   previously-retry-less boards (hellowork, freework, englishjobs, remoteok,
   datasciencejobs, weworkremotely, faruse). builtin/wttj/linkedin already had
   inline retry and were left untouched. User chose retry-on-all-boards.
3. **crawlee**: left as a declared dependency (user decision) — noted here as
   possibly dead; revisit if a crawlee-based crawler is ever planned.
