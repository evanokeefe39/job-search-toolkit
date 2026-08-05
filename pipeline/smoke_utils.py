"""Shared utilities for smoke testing and cost estimation across pipeline stages."""

from __future__ import annotations

from .config import LLM_MODEL

# Approximate token costs per model (USD per 1M tokens, input/output)
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}


def estimate_cost(
    num_items: int,
    tokens_in_per_item: int,
    tokens_out_per_item: int,
    label: str = "",
) -> dict:
    """Estimate LLM cost for a batch operation."""
    cost_in, cost_out = MODEL_COSTS.get(LLM_MODEL, MODEL_COSTS["gpt-4o-mini"])
    total_in = num_items * tokens_in_per_item
    total_out = num_items * tokens_out_per_item
    cost = (total_in / 1_000_000) * cost_in + (total_out / 1_000_000) * cost_out

    return {
        "model": LLM_MODEL,
        "num_items": num_items,
        "est_tokens_in": total_in,
        "est_tokens_out": total_out,
        "est_cost_usd": round(cost, 4),
        "label": label,
    }


def print_estimate(est: dict) -> None:
    """Print a cost estimate to stdout."""
    label = f" ({est['label']})" if est.get("label") else ""
    print(
        f"Cost estimate{label}: ~${est['est_cost_usd']:.4f} USD "
        f"({est['est_tokens_in']:,} in / {est['est_tokens_out']:,} out tokens, "
        f"model={est['model']})"
    )
