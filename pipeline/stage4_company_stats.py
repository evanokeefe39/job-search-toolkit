"""Stage 4: Company stats — size, stock performance, news.

Uses LLM for company identification + yfinance for deterministic stock data.
SPARSE: Most posting companies are private French SMBs. Stock data will be null for ~95%.

Idempotent — skips jobs that already have `company_stats`.
Reads from and writes to `freework_jobs_enriched.json`.

Usage:
    python -m pipeline.stage4_company_stats                # process all
    python -m pipeline.stage4_company_stats --smoke 3      # smoke test: 3 companies
    python -m pipeline.stage4_company_stats --dry-run      # show what would run
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .config import ENRICHED_JOBS
from .llm_client import LLMClient

# Approximate token costs per model (USD per 1M tokens, input/output)
MODEL_COSTS = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "deepseek-chat": (0.27, 1.10),
}

COMPANY_RESEARCH_SYSTEM = """You are a business analyst identifying companies for a job seeker.
Given a company name and optionally its sector, identify the company and determine if it is publicly traded.

Return a JSON object:
{
  "researched_company": "string — the canonical name of the company researched",
  "company_size": "string or null — approximate employee count range: '1-50', '51-200', '201-1000', '1001-5000', '5001-10000', '10000+'",
  "company_type": "startup" | "scaleup" | "mid_size" | "enterprise" | "consulting_firm" | "unknown",
  "is_public": true | false | null,
  "stock_ticker": "string or null — ticker symbol with exchange suffix (e.g., 'MC.PA' for LVMH on Euronext Paris, 'SAN.PA' for Sanofi). Use Yahoo Finance conventions.",
  "headquarters": "string or null — HQ city/country",
  "founded": "string or null — founding year",
  "notable_news": ["string — 1-2 notable recent events known about this company, or empty list"],
  "reputation_summary": "string — 1-sentence summary of reputation as an employer in France/Europe",
  "info_quality": "high" | "medium" | "low" | "unknown"
}

Rules:
- For French CAC 40 / SBF 120 companies: use '.PA' suffix for tickers (e.g., BNP.PA, OR.PA, AI.PA).
- For US-listed companies: use standard ticker (e.g., GOOGL, MSFT, JPM).
- For Euronext non-Paris: use '.AS' (Amsterdam), '.BR' (Brussels), etc.
- For consulting/ESN firms (SSII/société de conseil): set company_type to "consulting_firm", is_public to false.
- If you cannot confidently identify the company, set info_quality to "unknown" and use null/empty for other fields.
- Be conservative: only set is_public=true if you are confident the company is listed.
- Output ONLY the JSON object."""


def load_jobs(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_jobs(path: Path, jobs: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def _build_prompt(job: dict) -> str:
    end_client = job.get("end_client_name")
    sector = job.get("end_client_sector", "")
    company = job.get("company", "")

    if end_client:
        return (
            f"Company to research: {end_client}\n"
            f"Industry sector: {sector}\n"
            f"(This job was posted by consulting firm: {company})"
        )
    else:
        return (
            f"Company to research: {company}\n"
            f"Industry sector (inferred): {sector}"
        )


def _fetch_stock_perf(ticker: str) -> dict | None:
    """Fetch 12-month stock performance using yfinance. Returns None if unavailable."""
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")

        if hist.empty:
            return None

        start_price = float(hist["Close"].iloc[0])
        end_price = float(hist["Close"].iloc[-1])
        high_52w = float(hist["Close"].max())
        low_52w = float(hist["Close"].min())
        perf_pct = round((end_price - start_price) / start_price * 100, 1)

        # Get current market cap
        info = stock.info or {}
        market_cap = info.get("marketCap")
        currency = info.get("currency", "USD")

        return {
            "ticker": ticker,
            "price_current": round(end_price, 2),
            "price_52w_high": round(high_52w, 2),
            "price_52w_low": round(low_52w, 2),
            "perf_12m_pct": perf_pct,
            "market_cap": market_cap,
            "currency": currency,
            "data_source": "yfinance",
            "data_date": hist.index[-1].strftime("%Y-%m-%d"),
            "disclaimer": "Stock data from Yahoo Finance. May be delayed.",
        }
    except Exception as e:
        return {"error": str(e), "data_source": "yfinance"}


def _cost_estimate(num_companies: int) -> dict:
    """Estimate LLM costs for this stage."""
    from .config import LLM_MODEL

    cost_per_1m_in, cost_per_1m_out = MODEL_COSTS.get(
        LLM_MODEL, MODEL_COSTS["gpt-4o-mini"]
    )
    # Prompt ~500 tokens, system ~150, output ~200 tokens
    tokens_in = num_companies * 650
    tokens_out = num_companies * 200
    cost = (tokens_in / 1_000_000) * cost_per_1m_in + (
        tokens_out / 1_000_000
    ) * cost_per_1m_out

    return {
        "model": LLM_MODEL,
        "num_companies": num_companies,
        "est_tokens_in": tokens_in,
        "est_tokens_out": tokens_out,
        "est_cost_usd": round(cost, 4),
        "note": "Stock data via yfinance is free (no API cost).",
    }


async def enrich_company_stats(
    jobs: list[dict],
    smoke: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    llm = LLMClient()

    # Deduplicate by research target
    research_targets: dict[str, list[int]] = defaultdict(list)
    for i, job in enumerate(jobs):
        if job.get("company_stats") is not None:
            continue
        target = (job.get("end_client_name") or job.get("company") or "unknown").strip()
        if target:
            research_targets[target].append(i)

    targets = list(research_targets.keys())
    total_jobs_affected = sum(len(research_targets[t]) for t in targets)

    # Print cost estimate
    est = _cost_estimate(len(targets))
    print(f"Cost estimate: ~${est['est_cost_usd']:.4f} USD "
          f"({est['est_tokens_in']:,} in / {est['est_tokens_out']:,} out tokens, "
          f"model={est['model']})")
    print(f"Unique companies to research: {len(targets)} "
          f"(affecting {total_jobs_affected} jobs)")

    if smoke is not None:
        targets = targets[:smoke]
        print(f"SMOKE MODE: only processing {len(targets)} companies")

    if dry_run:
        for t in targets[:10]:
            print(f"  {t}")
        return jobs

    if not targets:
        print("Nothing to research.")
        return jobs

    # Phase 1: LLM research to get company info + ticker
    batch_size = 5
    for batch_start in range(0, len(targets), batch_size):
        batch_targets = targets[batch_start : batch_start + batch_size]
        prompts = []
        for target in batch_targets:
            idx = research_targets[target][0]
            prompts.append(_build_prompt(jobs[idx]))

        results = await llm.batch_complete_json(
            prompts,
            system=COMPANY_RESEARCH_SYSTEM,
            temperature=0.3,
            max_tokens=512,
        )

        for target, result in zip(batch_targets, results):
            for idx in research_targets[target]:
                jobs[idx]["company_stats"] = dict(result)
                jobs[idx]["company_stats"]["_researched_as"] = target

        print(
            f"  LLM research: {batch_start + len(batch_targets)} / {len(targets)}"
        )

    await llm.close()

    # Save intermediate results so LLM spend isn't lost if yfinance phase crashes
    save_jobs(ENRICHED_JOBS, jobs)
    print("  (saved intermediate results after LLM phase)")

    # Phase 2: yfinance for public companies (deterministic, free)
    public_tickers = set()
    ticker_to_targets: dict[str, list[str]] = defaultdict(list)
    for target, indices in research_targets.items():
        if not indices:
            continue
        stats = jobs[indices[0]].get("company_stats", {})
        ticker = stats.get("stock_ticker")
        if ticker and stats.get("is_public"):
            public_tickers.add(ticker)
            ticker_to_targets[ticker].append(target)

    if public_tickers:
        print(f"\nFetching stock data for {len(public_tickers)} tickers: {sorted(public_tickers)}")
        for ticker in sorted(public_tickers):
            perf = _fetch_stock_perf(ticker)
            if perf and "error" not in perf:
                print(f"  {ticker}: {perf['perf_12m_pct']:+.1f}% 12m, "
                      f"€{perf['price_current']:.2f} "
                      f"(52w: {perf['price_52w_low']:.2f}–{perf['price_52w_high']:.2f})")
            else:
                print(f"  {ticker}: no data ({(perf or {}).get('error', 'unknown')})")

            # Apply to all jobs for that company
            for target in ticker_to_targets.get(ticker, []):
                for idx in research_targets[target]:
                    cs = jobs[idx].get("company_stats", {})
                    cs["stock_performance"] = perf
                    jobs[idx]["company_stats"] = cs
    else:
        print("\nNo public companies identified — skipping stock data fetch.")

    return jobs


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 4: Enrich with company stats (LLM + yfinance)"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke", type=int, default=None,
                        help="Smoke test: process only N companies")
    parser.add_argument("--input", type=Path, default=ENRICHED_JOBS)
    parser.add_argument("--output", type=Path, default=ENRICHED_JOBS)
    args = parser.parse_args()

    jobs = load_jobs(args.input)
    if not jobs:
        print(f"No jobs found in {args.input}")
        sys.exit(1)

    enriched = await enrich_company_stats(
        jobs, smoke=args.smoke, dry_run=args.dry_run
    )

    if not args.dry_run:
        save_jobs(args.output, enriched)
        print(f"Saved {len(enriched)} jobs to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
