import unittest

from lolita_radar.crawler import enrich_source_runs
from lolita_radar.feed import build_home_feed
from lolita_radar.trend import build_trend_feed


class FeedOsTests(unittest.TestCase):
    def test_home_feed_builds_four_streams(self) -> None:
        events = [
            {
                "source": "angelic_pretty",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "Shell Garden JSK",
                "url": "https://example.com/ap",
                "created_at": "2026-06-30T10:00:00+00:00",
            },
            {
                "source": "generic_page",
                "event_type": "content_changed",
                "status": "shop_news",
                "title": "Proxy JSK 预约",
                "url": "https://example.com/shop",
                "created_at": "2026-06-30T10:01:00+00:00",
                "metadata": {"matched_keywords": ["JSK", "预约"]},
            },
        ]
        market_summary = {
            "brands": [
                {"brand_alias": "AP", "sample_count": 3, "avg_premium_rate": 0.45, "max_premium_rate": 0.7}
            ]
        }
        market_alerts = {"alerts": [{"kind": "sample_gap", "alias": "BABY", "reason": "core_needs_samples"}]}

        feed = build_home_feed(events, [], market_summary, market_alerts, [], [])

        self.assertEqual(feed["summary"]["drops"], 1)
        self.assertEqual(feed["summary"]["shops"], 1)
        self.assertEqual(feed["summary"]["trends"], 1)
        self.assertGreaterEqual(feed["summary"]["alerts"], 2)
        self.assertEqual(feed["streams"]["release"][0]["brand"], "AP")
        self.assertEqual(feed["streams"]["drop"][0]["feed_type"], "drop")
        self.assertIn("keywords: JSK, 预约", feed["streams"]["drop"][0]["meta"])
        self.assertIn("keyword_match", feed["streams"]["drop"][0]["reason_codes"])
        self.assertEqual(feed["streams"]["trend"][0]["kind"], "rising")
        alert_titles = {row["title"] for row in feed["streams"]["alert"]}
        self.assertIn("Shell Garden JSK", alert_titles)
        self.assertNotIn("Proxy JSK 预约", alert_titles)

    def test_alert_feed_normalizes_high_premium_and_sample_gap(self) -> None:
        market_alerts = {
            "alerts": [
                {
                    "kind": "sample_spike",
                    "severity": "critical",
                    "alias": "AP",
                    "item_name": "Shell Garden JSK",
                    "premium_rate": 0.82,
                    "reason": "collector_premium",
                },
                {
                    "kind": "brand_heat",
                    "severity": "watch",
                    "alias": "BABY",
                    "title": "BABY",
                    "premium_rate": 0.55,
                    "reason": "brand_hot_average",
                },
                {
                    "kind": "sample_gap",
                    "severity": "sample_gap",
                    "alias": "AATP",
                    "title": "ALICE and the PIRATES",
                    "reason": "core_needs_samples",
                },
            ]
        }

        feed = build_home_feed([], [], {"brands": []}, market_alerts, [], [])
        alerts = feed["streams"]["alert"]

        self.assertEqual(alerts[0]["kind"], "high_premium")
        self.assertEqual(alerts[0]["title"], "Shell Garden JSK")
        self.assertIn("82% premium", alerts[0]["meta"])
        self.assertIn("sample_spike", alerts[0]["reason_codes"])
        self.assertEqual(alerts[1]["kind"], "high_premium")
        self.assertIn("brand_heat", alerts[1]["reason_codes"])
        self.assertEqual(alerts[2]["kind"], "sample_gap")
        self.assertIn("sample_gap", alerts[2]["reason_codes"])

    def test_trend_engine_outputs_direction_confidence_and_reasons(self) -> None:
        trends = build_trend_feed(
            {"brands": [{"brand_alias": "AP", "sample_count": 4, "avg_premium_rate": 0.5}]},
            [{"brand_alias": "AP", "direction": "rising", "observed_at": "2026-06-30"}],
            [{"source": "angelic_pretty", "status": "new_arrival"}],
        )

        self.assertEqual(trends[0]["kind"], "rising")
        self.assertGreaterEqual(trends[0]["confidence"], 60)
        self.assertEqual(trends[0]["avg_premium_rate"], 0.5)
        self.assertEqual(trends[0]["sample_count"], 4)
        self.assertIn("reason: sample_supported, premium_rising", trends[0]["meta"])
        self.assertIn("sample_supported", trends[0]["reason_codes"])
        self.assertIn("premium_rising", trends[0]["reason_codes"])

    def test_trend_engine_allows_only_rule_directions(self) -> None:
        trends = build_trend_feed(
            {"brands": [{"brand_alias": "AP", "sample_count": 3, "avg_premium_rate": -0.2}]},
            [{"brand_alias": "AP", "direction": "spiking", "observed_at": "2026-06-30"}],
            [{"source": "angelic_pretty", "status": "shop_news"}],
        )

        self.assertEqual(trends[0]["kind"], "cooling")
        self.assertIn("premium_cooling", trends[0]["reason_codes"])
        self.assertNotIn("release_activity", trends[0]["reason_codes"])

    def test_trend_engine_outputs_sample_gap_for_weighted_brand_without_samples(self) -> None:
        trends = build_trend_feed(
            {"brands": []},
            [],
            [],
            brand_weights=[{"alias": "AP", "weight": 100}],
        )

        self.assertEqual(trends[0]["brand"], "AP")
        self.assertEqual(trends[0]["kind"], "stable")
        self.assertIn("reason: sample_gap", trends[0]["meta"])
        self.assertIn("sample_gap", trends[0]["reason_codes"])

    def test_crawler_health_marks_failed_and_degraded(self) -> None:
        rows = enrich_source_runs(
            [
                {"source": "ap", "ok": True, "item_count": 0, "event_count": 0},
                {"source": "baby", "ok": False, "item_count": 0, "event_count": 0},
            ]
        )

        by_source = {row["source"]: row for row in rows}
        self.assertEqual(by_source["ap"]["status"], "degraded")
        self.assertEqual(by_source["baby"]["status"], "failed")
        self.assertEqual(by_source["baby"]["error_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
