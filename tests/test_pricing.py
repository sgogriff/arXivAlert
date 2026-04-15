from __future__ import annotations

import unittest

from src.pricing import estimate_cost_breakdown, get_pricing
from src.scorer import TokenUsage


class PricingTests(unittest.TestCase):
    def test_get_pricing_recognizes_real_model_ids(self) -> None:
        self.assertIsNotNone(get_pricing("claude-sonnet-4-20250514"))
        self.assertIsNotNone(get_pricing("claude-opus-4-1-20250805"))
        self.assertIsNotNone(get_pricing("claude-3-5-haiku-latest"))
        self.assertIsNotNone(get_pricing("claude-sonnet-4.5"))
        self.assertIsNotNone(get_pricing("claude-opus-4.6"))

    def test_estimate_cost_breakdown_handles_cache_ttls(self) -> None:
        usage = TokenUsage(
            input_tokens=100_000,
            output_tokens=10_000,
            cache_creation_input_tokens=70_000,
            cache_read_input_tokens=20_000,
            cache_creation_ephemeral_5m_input_tokens=50_000,
            cache_creation_ephemeral_1h_input_tokens=10_000,
        )

        estimate = estimate_cost_breakdown("claude-sonnet-4-20250514", usage)

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertAlmostEqual(estimate.input_cost_usd, 0.30)
        self.assertAlmostEqual(estimate.output_cost_usd, 0.15)
        self.assertAlmostEqual(estimate.cache_write_5m_cost_usd, 0.225)
        self.assertAlmostEqual(estimate.cache_write_1h_cost_usd, 0.06)
        self.assertAlmostEqual(estimate.cache_read_cost_usd, 0.006)
        self.assertAlmostEqual(estimate.total_usd, 0.741)


if __name__ == "__main__":
    unittest.main()
