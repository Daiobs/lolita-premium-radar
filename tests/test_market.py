import json
import tempfile
import unittest
from pathlib import Path

from lolita_radar.market import (
    append_market_observation,
    build_brand_weight_profile,
    build_opportunity_radar,
    build_pattern_radar,
    load_market_observations,
    premium_priority_score,
    premium_score_breakdown,
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
        self.assertIn("quality_score", summary["records"][0])
        self.assertEqual(summary["quality"]["sample_count"], 3)
        self.assertEqual(summary["quality"]["weak_count"], 3)

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

    def test_premium_score_breakdown_explains_total_score(self) -> None:
        breakdown = premium_score_breakdown(0.4, brand_weight=100, sample_count=3)

        self.assertEqual(breakdown["premium_points"], 22)
        self.assertEqual(breakdown["brand_points"], 40)
        self.assertEqual(breakdown["sample_points"], 6)
        self.assertEqual(premium_priority_score(0.4, brand_weight=100, sample_count=3), 68)

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
        self.assertIn("score_breakdown", opportunities[0])
        self.assertIn("strong_premium", opportunities[0]["reason_codes"])
        self.assertEqual(opportunities[1]["band"], "watch")

    def test_build_brand_weight_profile_explains_weight_and_evidence(self) -> None:
        profile = build_brand_weight_profile(
            brand_weights=[
                {
                    "alias": "AP",
                    "name": "Angelic Pretty",
                    "weight": 100,
                    "tier": "core",
                    "style": "sweet print",
                    "market_keywords": ["贝壳", "Holy Lantern"],
                },
                {"alias": "Meta", "name": "Metamorphose", "weight": 80, "tier": "watch"},
            ],
            market_brands=[
                {"brand_alias": "AP", "sample_count": 5, "avg_premium_rate": 0.5, "max_premium_rate": 0.9},
            ],
        )

        ap = next(row for row in profile if row["alias"] == "AP")
        meta = next(row for row in profile if row["alias"] == "Meta")
        self.assertEqual(ap["weight_band"], "core")
        self.assertEqual(ap["weight_role"], "release_priority")
        self.assertEqual(ap["evidence_level"], "ready")
        self.assertEqual(ap["evidence_score"], 100)
        self.assertEqual(ap["market_keywords"], ["贝壳", "Holy Lantern"])
        self.assertIn("score_breakdown", ap)
        self.assertEqual(meta["evidence_level"], "missing")

    def test_build_pattern_radar_matches_market_keywords(self) -> None:
        patterns = build_pattern_radar(
            brand_weights=[
                {
                    "alias": "AP",
                    "name": "Angelic Pretty",
                    "weight": 100,
                    "market_keywords": ["贝壳", "Holy Lantern"],
                }
            ],
            observations=[
                {
                    "brand_alias": "AP",
                    "item_name": "白贝壳 JSK",
                    "premium_rate": 0.6,
                    "resale_price": 1800,
                    "retail_price": 1000,
                    "currency": "CNY",
                    "source": "xianyu",
                    "url": "https://example.com/shell",
                    "notes": "with KC",
                },
                {"brand_alias": "AP", "item_name": "Holy Lantern OP", "premium_rate": 0.2},
                {"brand_alias": "BABY", "item_name": "贝壳 JSK", "premium_rate": 0.9},
            ],
        )

        shell = next(pattern for pattern in patterns if pattern["keyword"] == "贝壳")
        self.assertEqual(shell["alias"], "AP")
        self.assertEqual(shell["sample_count"], 1)
        self.assertEqual(shell["avg_premium_rate"], 0.6)
        self.assertGreater(shell["priority_score"], 0)
        self.assertEqual(shell["evidence"][0]["source"], "xianyu")
        self.assertEqual(shell["evidence"][0]["url"], "https://example.com/shell")
        self.assertEqual(shell["evidence"][0]["quality_score"], 77)


if __name__ == "__main__":
    unittest.main()
