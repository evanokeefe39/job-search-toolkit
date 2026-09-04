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
| **linkedin_jobs** | ~177s | ~90 per-URL fetches, serial, fresh client each | latency | **YES** (~4.8x measured) |
| **linkedin_posts** | ~82s | same `_run_pass` loop as jobs | latency | **YES** (same loop) |
| wttj | ~92s | ~200 offer fetches, serial + paced | **rate-limit** | **NO** (0/8 under pool; HTTP 202) |
| hiringcafe | ~16s | curl_cffi, low volume | — | negligible |
| freework | ~9s | low volume | — | negligible |
| englishjobs/wwr/faruse/remoteok/builtin | 1-5s | tiny | — | not worth it |

## The decisive new finding: LinkedIn is latency-bound and parallelizable

Unlike wttj, LinkedIn job/post page fetches tolerate concurrency with no
throttling. Measured on 8 real `/jobs/view/` URLs:

| Mode | 8 URLs | Result |
|---|---|---|
| serial (current: fresh `httpx.Client` per URL) | 9.1s (~1.1s each) | 8/8 ok |
| **4-worker pool + shared client** | **1.9s** | **8/8 ok, zero 429/block** |

~4.8x faster with no anti-bot penalty. Two compounding inefficiencies in the
current code (`scrapers/linkedin/adapter.py` `_run_pass`):
1. `for result in urls: fetch_page(url)` — one serial round-trip per URL.
2. `fetch_page(url)` with no shared client creates a **fresh `httpx.Client`
   per call** (`fetch.py` `owns_client=True` path) — a new TCP+TLS handshake
   per URL is much of the ~1.1s.

Both jobs (`linkedin_jobs`) and posts (`linkedin_posts`) flow through the
SAME `_run_pass` fetch loop, so **one change** (bounded worker pool + a shared
client threaded through `_run_pass`) fixes both boards (~260s combined).

## What to implement (when approved)

In `scrapers/linkedin/adapter.py` `_run_pass`: replace the serial
`for result in urls: fetch_page(url, client=None)` with a bounded
`ThreadPoolExecutor` that (a) shares ONE `httpx.Client` across workers (created
once in `_run_pass` and passed to every `fetch_page`) and (b) preserves URL
order + stale/failed classification. Reuse the repo's thread-pool convention
(`concurrent.futures`, bounded workers). Respect order-sensitivity: France
filtering per job is pure per-record, so order does not matter for correctness,
but keep it deterministic for stable output.

CAVEAT: LinkedIn is anti-bot sensitive and the guest API is undocumented/
fragile (see `docs/linkedin-source-spike.md`). Use a modest worker count
(4-6, matching the probe) and keep the existing retry/backoff in `fetch_page`.
Probe on a live run before finalizing the worker count.

## Sources NOT worth it

- **wttj**: rate-limit-bound (HTTP 202 under any parallelism) — keep serial
  pacing. Negative result recorded in `tasks/lessons.md`.
- **hiringcafe / freework / thin boards**: already small (1-16s); a worker
  pool adds complexity for negligible gain.

## Estimated impact

Parallelizing the linkedin `_run_pass` fetch (~260s -> ~60-70s) on top of the
hellowork fix would take the pipeline from ~10m49s to roughly **~8min**.
Combined with hellowork's fix, that's ~21min -> ~8min overall (~2.6x).

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
