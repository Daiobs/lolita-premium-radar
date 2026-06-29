import json
import tempfile
import unittest
from pathlib import Path

from lolita_radar.market import load_market_observations, summarize_market_observations


class MarketTests(unittest.TestCase):
    def test_load_market_observations_computes_premium_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "market.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "brand_alias": "AP",
                            "item_name": "Rose JSK",
                            "retail_price": 2000,
                            "resale_price": 3000,
                            "currency": "CNY",
                        },
                        {"brand_alias": "bad", "item_name": "missing price"},
                    ]
                ),
                encoding="utf-8",
            )

            observations = load_market_observations(path)

            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]["brand_alias"], "AP")
            self.assertEqual(observations[0]["premium_rate"], 0.5)

    def test_summarize_market_observations_groups_by_brand(self) -> None:
        observations = [
            {"brand_alias": "AP", "retail_price": 1000, "resale_price": 1300, "premium_rate": 0.3, "currency": "CNY"},
            {"brand_alias": "AP", "retail_price": 1000, "resale_price": 1700, "premium_rate": 0.7, "currency": "CNY"},
            {"brand_alias": "BABY", "retail_price": 1000, "resale_price": 1200, "premium_rate": 0.2, "currency": "CNY"},
        ]

        summary = summarize_market_observations(observations)

        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["brands"][0]["brand_alias"], "AP")
        self.assertEqual(summary["brands"][0]["avg_premium_rate"], 0.5)
        self.assertEqual(summary["records"][0]["premium_rate"], 0.7)


if __name__ == "__main__":
    unittest.main()
