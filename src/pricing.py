"""Model pricing and cost estimation utilities."""

from __future__ import annotations

from dataclasses import dataclass

from src.scorer import TokenUsage


@dataclass(frozen=True)
class ModelPricing:
    """Pricing in USD per million tokens (MTok)."""

    model_family: str
    input_per_mtok: float
    output_per_mtok: float
    cache_write_multiplier_5m: float = 1.25
    cache_read_multiplier: float = 0.10
    as_of: str = "2026-03-26"


# Prices sourced from Anthropic Claude Docs pricing page (USD / MTok) as of 2026-03-26.
# Update this table if Anthropic changes pricing.
_PRICING_TABLE: dict[str, ModelPricing] = {
    # Claude Sonnet 4 family
    "claude-sonnet-4": ModelPricing("claude-sonnet-4", input_per_mtok=3.0, output_per_mtok=15.0),
    # Claude Sonnet 4.5 family
    "claude-sonnet-4.5": ModelPricing("claude-sonnet-4.5", input_per_mtok=3.0, output_per_mtok=15.0),
    # Claude Opus 4 family
    "claude-opus-4": ModelPricing("claude-opus-4", input_per_mtok=15.0, output_per_mtok=75.0),
    "claude-opus-4.1": ModelPricing("claude-opus-4.1", input_per_mtok=15.0, output_per_mtok=75.0),
    # Legacy / smaller models (kept for completeness)
    "claude-haiku-3.5": ModelPricing("claude-haiku-3.5", input_per_mtok=0.80, output_per_mtok=4.0),
    "claude-haiku-3": ModelPricing("claude-haiku-3", input_per_mtok=0.25, output_per_mtok=1.25),
}


def _model_family(model: str) -> str:
    m = model.strip()
    # Common pattern: "claude-sonnet-4-YYYYMMDD"
    for family in _PRICING_TABLE:
        if m == family or m.startswith(family + "-"):
            return family
    return m


def get_pricing(model: str) -> ModelPricing | None:
    return _PRICING_TABLE.get(_model_family(model))


def estimate_cost_usd(model: str, usage: TokenUsage) -> tuple[float | None, ModelPricing | None]:
    pricing = get_pricing(model)
    if pricing is None:
        return None, None

    base_input_cost = usage.input_tokens * pricing.input_per_mtok / 1_000_000
    output_cost = usage.output_tokens * pricing.output_per_mtok / 1_000_000

    # We use 5-minute cache write pricing as an estimate (Anthropic default).
    cache_write_cost = (
        usage.cache_creation_input_tokens
        * pricing.input_per_mtok
        * pricing.cache_write_multiplier_5m
        / 1_000_000
    )
    cache_read_cost = (
        usage.cache_read_input_tokens
        * pricing.input_per_mtok
        * pricing.cache_read_multiplier
        / 1_000_000
    )

    return base_input_cost + output_cost + cache_write_cost + cache_read_cost, pricing

