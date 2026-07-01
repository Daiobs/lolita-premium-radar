import json
import tempfile
import unittest
from pathlib import Path

from lolita_radar.market import (
    append_market_observation,
    build_market_alerts,
    build_market_momentum,
    load_market_observations,
    premium_band,
    premium_priority_score,
    premium_score_breakdown,
    summarize_market_observations,
)
from lolita_radar.trend import build_brand_signal_profile, build_pattern_trends, build_sample_backlog, build_trend_candidates


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
        self.assertEqual(summary["brands"][0]["avg_spread"], 500)
        self.assertEqual(summary["brands"][0]["min_retail_price"], 1000)
        self.assertEqual(summary["brands"][0]["max_retail_price"], 1000)
        self.assertEqual(summary["brands"][0]["min_resale_price"], 1300)
        self.assertEqual(summary["brands"][0]["max_resale_price"], 1700)
        self.assertEqual(summary["brands"][0]["premium_band"], "hot")
        self.assertGreater(summary["brands"][0]["priority_score"], summary["brands"][1]["priority_score"])
        self.assertEqual(summary["records"][0]["premium_rate"], 0.7)
        self.assertIn("priority_score", summary["records"][0])
        self.assertEqual(summary["records"][0]["premium_band"], "hot")
        self.assertEqual(next(row for row in summary["premium_bands"] if row["band"] == "hot")["count"], 1)
        self.assertEqual(next(row for row in summary["premium_bands"] if row["band"] == "premium")["count"], 1)
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

    def test_premium_band_segments_market_samples(self) -> None:
        self.assertEqual(premium_band(0.95), "collector")
        self.assertEqual(premium_band(0.5), "hot")
        self.assertEqual(premium_band(0.25), "premium")
        self.assertEqual(premium_band(0), "near_retail")
        self.assertEqual(premium_band(-0.2), "discount")

    def test_build_trend_candidates_labels_next_action(self) -> None:
        candidates = build_trend_candidates(
            brand_weights=[
                {"alias": "AP", "name": "Angelic Pretty", "weight": 100, "tier": "core", "style": "sweet print"},
                {"alias": "Meta", "name": "Metamorphose", "weight": 86, "tier": "watch", "style": "release/restock"},
            ],
            market_brands=[
                {"brand_alias": "AP", "sample_count": 3, "avg_premium_rate": 0.7, "max_premium_rate": 0.9},
                {"brand_alias": "Meta", "sample_count": 2, "avg_premium_rate": 0.1, "max_premium_rate": 0.2},
            ],
        )

        self.assertEqual(candidates[0]["alias"], "AP")
        self.assertEqual(candidates[0]["band"], "lead")
        self.assertIn("score_breakdown", candidates[0])
        self.assertIn("strong_premium", candidates[0]["reason_codes"])
        self.assertEqual(candidates[1]["band"], "watch")

    def test_build_brand_signal_profile_explains_weight_and_evidence(self) -> None:
        profile = build_brand_signal_profile(
            brand_weights=[
                {
                    "alias": "AP",
                    "name": "Angelic Pretty",
                    "weight": 100,
                    "tier": "core",
                    "style": "sweet print",
                    "market_keywords": ["贝壳", "Holy Lantern"],
                    "visual": {"accent": "#b4576f", "motif": "ribbon / shell print"},
                    "watch_urls": [{"label": "闲鱼", "url": "https://www.goofish.com/search?q=Angelic+Pretty+lolita"}],
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
        self.assertEqual(ap["visual"]["motif"], "ribbon / shell print")
        self.assertEqual(ap["watch_urls"][0]["label"], "闲鱼")
        self.assertIn("score_breakdown", ap)
        self.assertEqual(meta["evidence_level"], "missing")

    def test_build_sample_backlog_prioritizes_core_gaps(self) -> None:
        backlog = build_sample_backlog(
            brand_weights=[
                {
                    "alias": "AP",
                    "name": "Angelic Pretty",
                    "weight": 100,
                    "tier": "core",
                    "market_keywords": ["贝壳", "Holy Lantern"],
                    "watch_urls": [{"label": "闲鱼", "url": "https://www.goofish.com/search?q=Angelic+Pretty"}],
                },
                {"alias": "Meta", "name": "Metamorphose", "weight": 80, "tier": "watch"},
            ],
            market_brands=[
                {"brand_alias": "Meta", "sample_count": 2, "avg_premium_rate": 0.1},
            ],
        )

        self.assertEqual(backlog[0]["alias"], "AP")
        self.assertEqual(backlog[0]["target_samples"], 5)
        self.assertEqual(backlog[0]["missing_samples"], 5)
        self.assertEqual(backlog[0]["urgency"], "critical")
        self.assertEqual(backlog[0]["next_action"], "seed")
        self.assertEqual(backlog[0]["market_keywords"], ["贝壳", "Holy Lantern"])
        self.assertEqual(backlog[0]["watch_urls"][0]["label"], "闲鱼")

    def test_build_market_alerts_prioritizes_spikes_and_sample_gaps(self) -> None:
        summary = summarize_market_observations(
            [
                {
                    "brand_alias": "AP",
                    "item_name": "白贝壳 JSK",
                    "retail_price": 1000,
                    "resale_price": 1900,
                    "premium_rate": 0.9,
                    "currency": "CNY",
                    "source": "xianyu",
                    "url": "https://example.com/shell",
                    "observed_at": "2026-06-29",
                    "condition": "used",
                }
            ],
            brand_weights=[
                {"alias": "AP", "name": "Angelic Pretty", "weight": 100},
                {"alias": "BABY", "name": "BABY", "weight": 95},
            ],
        )

        alerts = build_market_alerts(
            brand_weights=[
                {
                    "alias": "AP",
                    "name": "Angelic Pretty",
                    "weight": 100,
                    "watch_urls": [{"label": "闲鱼", "url": "https://example.com/ap-watch"}],
                },
                {
                    "alias": "BABY",
                    "name": "BABY",
                    "weight": 95,
                    "watch_urls": [{"label": "闲鱼", "url": "https://example.com/baby-watch"}],
                },
            ],
            market_summary=summary,
        )

        self.assertGreaterEqual(alerts["summary"]["total"], 2)
        self.assertEqual(alerts["alerts"][0]["kind"], "sample_spike")
        self.assertEqual(alerts["alerts"][0]["severity"], "critical")
        self.assertEqual(alerts["alerts"][0]["url"], "https://example.com/shell")
        baby_gap = next(alert for alert in alerts["alerts"] if alert["kind"] == "sample_gap" and alert["alias"] == "BABY")
        self.assertEqual(baby_gap["url"], "")

    def test_build_pattern_trends_matches_market_keywords(self) -> None:
        patterns = build_pattern_trends(
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

    def test_build_market_momentum_compares_latest_to_previous_average(self) -> None:
        momentum = build_market_momentum(
            observations=[
                {
                    "brand_alias": "AP",
                    "item_name": "白贝壳 JSK",
                    "premium_rate": 0.2,
                    "observed_at": "2026-06-01",
                    "currency": "CNY",
                },
                {
                    "brand_alias": "AP",
                    "item_name": "白贝壳 OP",
                    "premium_rate": 0.7,
                    "observed_at": "2026-06-29",
                    "source": "xianyu",
                    "currency": "CNY",
                },
                {
                    "brand_alias": "BABY",
                    "item_name": "Kumya JSK",
                    "premium_rate": 0.4,
                    "observed_at": "2026-06-10",
                },
            ],
            brand_weights=[{"alias": "AP", "weight": 100}, {"alias": "BABY", "weight": 95}],
        )

        self.assertEqual(len(momentum), 1)
        self.assertEqual(momentum[0]["brand_alias"], "AP")
        self.assertEqual(momentum[0]["latest_item"], "白贝壳 OP")
        self.assertEqual(momentum[0]["previous_premium_rate"], 0.2)
        self.assertEqual(momentum[0]["latest_premium_rate"], 0.7)
        self.assertEqual(momentum[0]["delta"], 0.5)
        self.assertEqual(momentum[0]["direction"], "rising")
        self.assertEqual(momentum[0]["brand_weight"], 100)
        self.assertGreater(momentum[0]["priority_score"], 0)


if __name__ == "__main__":
    unittest.main()
