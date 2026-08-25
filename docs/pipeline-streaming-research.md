# Streaming vs batching for the JD pipeline (research, 2026-08-25)

**Question:** how should incremental streaming/batching of scrape results work —
is Dagster the right technology, or do we need a complementary streaming tool
(Kafka, Flink, queues, etc.)?

**Answer in one line:** Dagster is the right orchestrator, but it is a *batch*
tool — "streaming" for our scrape workloads means writing incrementally within
each asset and slicing work with Dagster's native **partitions**, not adding a
message broker or stream engine.

## Sources

- Dagster: "Data Ingestion Patterns: Push, Pull & Poll Explained"
  (2025-12-15) — dagster.io/blog/data-ingestion-patterns-when-to-use-push-pull-and-poll
- "Building scraping pipelines with Dagster in 2026" (2026-05-07) —
  dataresearchtools.com/scraping-with-dagster-2026/
- "Kafka vs Airflow vs Dagster: When to Use Each" — medium.com/codex
  (Kafka ≠ orchestrator; solves a different problem)
- Dagster issue #32853, #34013 — partitioned-asset semantics around
  per-partition materialization

## Findings

### 1. Dagster is batch-only; its "streaming" is partition-based

Dagster's mechanisms for slicing/incremental work are **partitioned assets**
(`StaticPartitionsDefinition`, `DailyPartitionsDefinition`,
`MultiPartitionsDefinition`), **IO managers** (separate compute from storage),
**sensors** (event-driven triggering), and **backfills**. Each asset
materializes as a unit; there is no record-at-a-time stream inside an asset.
For very high-frequency ingestion (1000s/sec) the asset model adds overhead —
that is explicitly called out as a place Dagster does not lead.

### 2. Our scrapes are bounded pull-based batches, not streams

Our board scrapers are **pull-based** (we initiate, control schedules, backfill
naturally). None of them is an unbounded event stream. The "loss" problem is
not a lack of streaming tech — it is that `scrape()` buffers the whole board in
memory and writes **once at the end**, so any crash loses everything fetched
so far. That is a code-level resilience bug, fixed by writing incrementally
(per-page/what-you-have) and idempotent upserts — no broker required.

### 3. Dagster partitions are the idiomatic fix for all-or-nothing ingest + no-resume

The two issues logged this session (all-or-nothing ingest; orphaned bronze with
no resume) are exactly what Dagster **partitioned assets** solve natively:

- A `StaticPartitionsDefinition` over boards gives one materialization per
  board — a failed board affects only its partition; others flow independently
  (per-board independence without hand-rolled `--boards` selection).
- Partitions give native **backfill / resume**: re-materialize just the failed
  partition instead of a fresh full run. This subsumes the "resume from
  bronze" ingest CLI with a cleaner mechanism.

Our current design uses a poor-man's substitute — a single `silver_upsert`
gated on all boards, `--boards` selection via `AssetSelection`, and (planned) a
separate `pipeline ingest` CLI. All three are re-implementing what Dagster
partitions provide out of the box.

### 4. Complementary tech is only warranted if latency/volume grows

If scrape volume or freshness demands true event-streaming (records flowing
continuously, sub-minute latency at thousands/sec), the standard stack is a
queue (Kafka / Pulsar) + a stream processor (Flink). For our scale (hundreds of
listings, hourly/daily cadence, DuckDB warehouse) that is massive overkill and
adds operational burden with zero benefit. The ingestion-patterns post's
poll/pull guidance confirms: pick the queue only when you need near-real-time
responsiveness with low latency — we do not.

### 5. Concrete recommendations

- **Keep Dagster.** Model each board as a **partitioned asset** (e.g. a static
  partition per board, and/or a daily partition per scrape) rather than a
  flat all-boards asset + `--boards` selection. This natively gives per-board
  independence, backfill, and resume — collapsing issues 1 & 2 into one
  Dagster-idiomatic feature.
- **Write incrementally inside scrape assets** (in-progress plan:
  datasciencejobs-streaming-landing). No streaming tech.
- **Land to DuckDB landing tables directly** (via `dagster-duckdb` IOManager or
  per-partition landing tables) instead of a single JSON file written at the
  end — partial runs survive and are trivially resumable.
- **Reconsider the `pipeline ingest` CLI** in favor of Dagster partition
  backfill if we adopt partitioned assets; otherwise keep the CLI as a
  pragmatic recovery path.
- **Freshness:** Dagster has native **freshness policies / data versioning**
  that could declare "assets older than N days are stale" — worth evaluating
  against our hand-built `STALE_AFTER_DAYS` staleness model.

## Not recommended now

- Adding Kafka / Pulsar / Flink / a message queue.
- Treating Dagster as a streaming engine (e.g. trying to stream records
  asset-to-asset).
- Per-record event processing at our scale.

## Decision / next step

The immediate fixes (per-board silver assets, resume-from-bronze CLI,
datasciencejobs incremental landing) remain the right near-term work. The
forward-looking recommendation is to migrate the hand-rolled per-board
selection + ingest CLI to **Dagster partitioned assets** (static partition per
board) so Dagster provides independence, backfill, and resume natively — this
is the cleaner, tech-appropriate realization of "streaming/batching of
results." Revisit a message-broker only if scrape latency/volume grows by
orders of magnitude.
