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
    cache_write_multiplier_1h: float = 2.0
    cache_read_multiplier: float = 0.10
    as_of: str = "2026-04-15"


@dataclass(frozen=True)
class CostEstimate:
    pricing: ModelPricing
    input_cost_usd: float
    output_cost_usd: float
    cache_write_5m_cost_usd: float
    cache_write_1h_cost_usd: float
    cache_read_cost_usd: float

    @property
    def total_usd(self) -> float:
        return (
            self.input_cost_usd
            + self.output_cost_usd
            + self.cache_write_5m_cost_usd
            + self.cache_write_1h_cost_usd
            + self.cache_read_cost_usd
        )


# Prices sourced from Anthropic Claude Docs pricing page (USD / MTok) as of 2026-04-15.
# Update this table if Anthropic changes pricing.
_PRICING_TABLE: dict[str, ModelPricing] = {
    # Claude Sonnet 4 family
    "claude-sonnet-4": ModelPricing("claude-sonnet-4", input_per_mtok=3.0, output_per_mtok=15.0),
    "claude-sonnet-4-6": ModelPricing("claude-sonnet-4-6", input_per_mtok=3.0, output_per_mtok=15.0),
    # Claude Sonnet 4.5 family
    "claude-sonnet-4-5": ModelPricing("claude-sonnet-4-5", input_per_mtok=3.0, output_per_mtok=15.0),
    # Claude Opus 4 family
    "claude-opus-4-6": ModelPricing("claude-opus-4-6", input_per_mtok=5.0, output_per_mtok=25.0),
    "claude-opus-4-5": ModelPricing("claude-opus-4-5", input_per_mtok=5.0, output_per_mtok=25.0),
    "claude-opus-4": ModelPricing("claude-opus-4", input_per_mtok=15.0, output_per_mtok=75.0),
    "claude-opus-4-1": ModelPricing("claude-opus-4-1", input_per_mtok=15.0, output_per_mtok=75.0),
    # Legacy / smaller models (kept for completeness)
    "claude-haiku-4-5": ModelPricing("claude-haiku-4-5", input_per_mtok=1.0, output_per_mtok=5.0),
    "claude-3-7-sonnet": ModelPricing("claude-3-7-sonnet", input_per_mtok=3.0, output_per_mtok=15.0),
    "claude-3-5-haiku": ModelPricing("claude-3-5-haiku", input_per_mtok=0.80, output_per_mtok=4.0),
    "claude-3-haiku": ModelPricing("claude-3-haiku", input_per_mtok=0.25, output_per_mtok=1.25),
}

_MODEL_ALIASES: dict[str, str] = {
    "claude-sonnet-4": "claude-sonnet-4",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-sonnet-4-5": "claude-sonnet-4-5",
    "claude-sonnet-4.5": "claude-sonnet-4-5",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-opus-4.6": "claude-opus-4-6",
    "claude-opus-4-5": "claude-opus-4-5",
    "claude-opus-4.5": "claude-opus-4-5",
    "claude-opus-4-1": "claude-opus-4-1",
    "claude-opus-4.1": "claude-opus-4-1",
    "claude-opus-4": "claude-opus-4",
    "claude-haiku-4-5": "claude-haiku-4-5",
    "claude-haiku-4.5": "claude-haiku-4-5",
    "claude-3-7-sonnet": "claude-3-7-sonnet",
    "claude-sonnet-3.7": "claude-3-7-sonnet",
    "claude-3-5-haiku": "claude-3-5-haiku",
    "claude-haiku-3.5": "claude-3-5-haiku",
    "claude-3-haiku": "claude-3-haiku",
    "claude-haiku-3": "claude-3-haiku",
}


def _model_family(model: str) -> str:
    m = model.strip()
    for prefix in sorted(_MODEL_ALIASES, key=len, reverse=True):
        if m == prefix or m.startswith(prefix + "-"):
            return _MODEL_ALIASES[prefix]
    return m


def get_pricing(model: str) -> ModelPricing | None:
    return _PRICING_TABLE.get(_model_family(model))


def estimate_cost_breakdown(model: str, usage: TokenUsage) -> CostEstimate | None:
    pricing = get_pricing(model)
    if pricing is None:
        return None

    cache_write_5m_tokens = usage.cache_creation_ephemeral_5m_input_tokens
    cache_write_1h_tokens = usage.cache_creation_ephemeral_1h_input_tokens
    cache_write_remainder = max(
        0,
        usage.cache_creation_input_tokens - cache_write_5m_tokens - cache_write_1h_tokens,
    )
    cache_write_5m_tokens += cache_write_remainder

    input_cost = usage.input_tokens * pricing.input_per_mtok / 1_000_000
    output_cost = usage.output_tokens * pricing.output_per_mtok / 1_000_000
    cache_write_5m_cost = (
        cache_write_5m_tokens
        * pricing.input_per_mtok
        * pricing.cache_write_multiplier_5m
        / 1_000_000
    )
    cache_write_1h_cost = (
        cache_write_1h_tokens
        * pricing.input_per_mtok
        * pricing.cache_write_multiplier_1h
        / 1_000_000
    )
    cache_read_cost = (
        usage.cache_read_input_tokens
        * pricing.input_per_mtok
        * pricing.cache_read_multiplier
        / 1_000_000
    )
    return CostEstimate(
        pricing=pricing,
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        cache_write_5m_cost_usd=cache_write_5m_cost,
        cache_write_1h_cost_usd=cache_write_1h_cost,
        cache_read_cost_usd=cache_read_cost,
    )


def estimate_cost_usd(model: str, usage: TokenUsage) -> tuple[float | None, ModelPricing | None]:
    estimate = estimate_cost_breakdown(model, usage)
    if estimate is None:
        return None, None
    return estimate.total_usd, estimate.pricing
