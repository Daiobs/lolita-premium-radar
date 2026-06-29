import json
import tempfile
import unittest
from pathlib import Path

from lolita_radar.market import (
    append_market_observation,
    build_opportunity_radar,
    load_market_observations,
    premium_priority_score,
    summarize_market_observations,
)


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

        summary = summarize_market_observations(
            observations,
            brand_weights=[
                {"alias": "AP", "weight": 100},
                {"alias": "BABY", "weight": 95},
            ],
        )

        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["brands"][0]["brand_alias"], "AP")
        self.assertEqual(summary["brands"][0]["avg_premium_rate"], 0.5)
        self.assertEqual(summary["brands"][0]["brand_weight"], 100)
        self.assertGreater(summary["brands"][0]["priority_score"], summary["brands"][1]["priority_score"])
        self.assertEqual(summary["records"][0]["premium_rate"], 0.7)
        self.assertIn("priority_score", summary["records"][0])

    def test_append_market_observation_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "market.json"
            path.write_text("[]\n", encoding="utf-8")

            observation = append_market_observation(
                path,
                {
                    "brand_alias": "baby",
                    "item_name": "Kumya JSK",
                    "retail_price": "1800",
                    "resale_price": "2520",
                    "currency": "CNY",
                },
            )
            saved = load_market_observations(path)

            self.assertEqual(observation["brand_alias"], "BABY")
            self.assertEqual(saved[0]["premium_rate"], 0.4)

    def test_premium_priority_score_combines_weight_and_premium(self) -> None:
        low_weight_score = premium_priority_score(0.4, brand_weight=50, sample_count=1)
        high_weight_score = premium_priority_score(0.4, brand_weight=100, sample_count=1)

        self.assertGreater(high_weight_score, low_weight_score)

    def test_build_opportunity_radar_labels_next_action(self) -> None:
        opportunities = build_opportunity_radar(
            brand_weights=[
                {"alias": "AP", "name": "Angelic Pretty", "weight": 100, "tier": "core", "style": "sweet print"},
                {"alias": "Meta", "name": "Metamorphose", "weight": 86, "tier": "watch", "style": "release/restock"},
            ],
            market_brands=[
                {"brand_alias": "AP", "sample_count": 3, "avg_premium_rate": 0.7, "max_premium_rate": 0.9},
                {"brand_alias": "Meta", "sample_count": 2, "avg_premium_rate": 0.1, "max_premium_rate": 0.2},
            ],
        )

        self.assertEqual(opportunities[0]["alias"], "AP")
        self.assertEqual(opportunities[0]["band"], "lead")
        self.assertIn("strong_premium", opportunities[0]["reason_codes"])
        self.assertEqual(opportunities[1]["band"], "watch")


if __name__ == "__main__":
    unittest.main()
