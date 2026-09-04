# Board parallelization spike — which sources can be optimized (2026-09-04)

Follow-on to the hellowork concurrency fix (#52, ~21min -> ~10m49s) and the
wttj probe (negative result, recorded in `tasks/lessons.md`). Goal: determine,
per source, whether bounded concurrency helps or hurts, before touching code.

Method: live probe each source — fetch ~8 real URLs serial vs a small
(2-4 worker) pool. A pool that triggers the source's throttle status
(202/429/403) means the source is **rate-limit-bound** (concurrency hurts);
otherwise it is **latency-bound** (concurrency helps, like hellowork).

## Per-source verdict (measured 2026-09-04)

| Board | Time share | Fetch profile | Bound | Concurrent win? |
|---|---|---|---|---|
| hellowork | ~222s (was 585s) | ~810 per-job detail fetches, serial | latency | **YES** (merged #52, ~2.6x) |
| **linkedin_jobs** | ~177s | ~90 per-URL fetches, serial, fresh client each | **rate-limit** | **NO** (429s lose jobs) |
| **linkedin_posts** | ~82s | same `_run_pass` loop as jobs | **rate-limit** | **NO** (same loop) |
| wttj | ~92s | ~200 offer fetches, serial + paced | **rate-limit** | **NO** (0/8 under pool; HTTP 202) |
| hiringcafe | ~16s | curl_cffi, low volume | — | negligible |
| freework | ~9s | low volume | — | negligible |
| englishjobs/wwr/faruse/remoteok/builtin | 1-5s | tiny | — | not worth it |

## CORRECTION 2026-09-04: LinkedIn is rate-limit-bound under sustained load

The 8-URL probe above was a FALSE POSITIVE — too small to trip LinkedIn's rate
limiter. Probing the REAL `_run_pass` (full discovery -> ~30 URLs per run) told
a different story. Under sustained concurrency LinkedIn returns HTTP 429 and
DROPS jobs:

| detail_concurrency | jobs | wall | 429s (fetch errors) |
|---|---|---|---|
| 1 (serial) | 28-30 | ~69s | 0 |
| 2 | 27 | ~62s | 1 (lost 1 job) |
| 3 | 27 | ~61s | 3 |
| **5** | **24-48** | ~29s | **52 (most fetches failed)** |

So LinkedIn is **partially rate-limit-bound** (tolerates brief bursts, 429s
under sustained concurrency). A worker pool at the shared default
`detail_concurrency=5` would SILENTLY LOSE jobs — worse than slow.

**Decision (human, 2026-09-04):** LinkedIn stays SERIAL. The one safe change
is reusing ONE shared `httpx.Client` across the serial fetches (removing the
per-call TLS handshake that inflated each fetch). Do NOT parallelize LinkedIn
fetches without a residential-proxy layer first.

## Residential-proxy principle (human directive, 2026-09-04)

For ANY rate-limited source going forward (linkedin, wttj, future boards),
before choosing concurrency, weigh using **Apify residential proxies** and the
**cost-benefit of the speed increase vs scalability it enables** — rather than
either (a) accepting job loss from naive concurrency or (b) staying slow
forever. Residential proxies decouple "many concurrent requests" from "your
one IP gets throttled," which is the real ceiling on scraping rate for
anti-bot sources. Trade-off to evaluate per source: proxy $/GB (or per-result)
vs the wall-clock saved and whether the board's volume justifies it. Cheap
boards (faruse, remoteok) never justify proxies; high-volume latency-bound
targets that are IP-throttled (linkedin at scale, wttj) are the candidates.

## Sources NOT worth it

- **wttj**: rate-limit-bound (HTTP 202 under any parallelism) — keep serial
  pacing. Negative result recorded in `tasks/lessons.md`.
- **hiringcafe / freework / thin boards**: already small (1-16s); a worker
  pool adds complexity for negligible gain.

## Estimated impact (corrected)

With hellowork already concurrent (#52) and wttj/linkedin both rate-limit-bound
(no safe worker pool without proxies), the remaining safe win is the
linkedin shared-client reuse (removing per-call TLS handshakes) — a modest but
real improvement. The earlier ~8min estimate assumed parallelizing linkedin,
which is NOT viable without a residential-proxy layer. Real further gains on
linkedin/wttj require the proxy cost-benefit decision above; hellowork remains
the one board where the worker pool paid off.

## Docker / deployment note (user question)

Running Dagster in Docker Desktop does NOT make this pipeline faster — the
remaining time is network I/O + per-source rate-limit waiting, not CPU. A real
dagster deployment (Docker: webserver + daemon + run launcher) only pays off
for (a) the multiprocess executor actually forking independent assets, and
(b) dagster-native scheduling — but a Windows Task Scheduler entry calling
`pipeline run` daily achieves (b) with zero infra. The Docker deployment IS
worth it once dagster is used across projects (e.g. `~/repos/datalake`) and
multiple schedules justify the operational weight — but treat it as a
multi-project scheduling/infra decision, not a runtime-optimization lever for
this single job-search pipeline.
