"""Token pricing + cost estimation for vision calls.

Prices are USD per 1M tokens (Anthropic first-party rates). Used both to
PRE-CHECK a call against the remaining match budget (estimate) and to charge the
ACTUAL cost from the response's token usage.
"""
from __future__ import annotations

# USD per 1,000,000 tokens: model_id -> (input, output).
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}

# Fallback used if a model isn't in the table (assume Sonnet-tier) so an unknown
# model over-estimates rather than under-charges the budget.
_FALLBACK = (3.00, 15.00)


def _rates(model: str) -> tuple[float, float]:
    return MODEL_PRICING.get(model, _FALLBACK)


def image_tokens(width: int, height: int) -> int:
    """Anthropic's image-token approximation: ~ (w*h)/750, capped."""
    return min(1600, max(1, round((width * height) / 750)))


def estimate_cost_usd(model: str, est_input_tokens: int, max_output_tokens: int) -> float:
    """Worst-case cost of a call BEFORE making it (assumes full output)."""
    cin, cout = _rates(model)
    return (est_input_tokens * cin + max_output_tokens * cout) / 1_000_000.0


def actual_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    cin, cout = _rates(model)
    return (input_tokens * cin + output_tokens * cout) / 1_000_000.0
