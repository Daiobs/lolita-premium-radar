import unittest
from pathlib import Path

from lolita_radar.feed import build_home_feed


class FeedOsAcceptanceTests(unittest.TestCase):
    def test_required_product_modules_exist(self) -> None:
        root = Path("src/lolita_radar")

        for name in ("feed", "trend", "shop", "crawler", "core"):
            self.assertTrue((root / name).is_dir(), name)

    def test_feed_contract_outputs_required_fields(self) -> None:
        events = [
            {
                "source": "angelic_pretty",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "Shell Garden JSK",
                "url": "https://example.com/release",
                "published_at": "2026-06-30",
                "metadata": {"price": "¥38,280"},
            },
            {
                "source": "generic_page",
                "event_type": "new_item",
                "status": "shop_news",
                "title": "Proxy page",
                "url": "https://example.com/drop",
                "metadata": {
                    "shop": {"name": "Proxy Shop"},
                    "item": {"title": "Shell Garden JSK", "url": "https://example.com/drop/item"},
                    "matched_keywords": ["JSK", "预约"],
                },
            },
        ]
        market_summary = {"brands": [{"brand_alias": "AP", "sample_count": 3, "avg_premium_rate": 0.5}]}
        source_runs = [
            {
                "source": "angelic_pretty",
                "status": "failed",
                "ok": False,
                "checked_at": "2026-06-30T00:00:00+00:00",
                "error_rate": 1.0,
                "latency_ms": 1200,
                "item_count": 0,
                "error_message": "timeout",
            }
        ]

        feed = build_home_feed(
            events,
            [],
            market_summary,
            {"alerts": []},
            [],
            source_runs,
            brand_weights=[{"alias": "AP", "watch_urls": [{"label": "market", "url": "https://example.com/market"}]}],
            source_urls={"angelic_pretty": "https://example.com/source"},
        )

        release = feed["streams"]["release"][0]
        drop = feed["streams"]["drop"][0]
        trend = feed["streams"]["trend"][0]
        alert = feed["streams"]["alert"][0]

        self.assert_required_keys(release, ("brand", "title", "type", "time", "price", "url"))
        self.assert_required_keys(drop, ("shop", "item", "keywords", "urgency", "url"))
        self.assert_required_keys(trend, ("brand", "trend", "confidence", "price_delta", "reason_codes"))
        self.assert_required_keys(alert, ("feed_type", "kind", "title", "reason_codes", "url"))
        self.assertEqual([row["feed_type"] for row in feed["all"][:4]], ["release", "drop", "alert", "trend"])

    def test_source_tree_keeps_forbidden_product_directions_out(self) -> None:
        haystack = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore").lower()
            for path in Path("src/lolita_radar").rglob("*.py")
            if path.name != "__pycache__"
        )

        for forbidden in ("northstar", "north star", "brandcrown", "salon"):
            self.assertNotIn(forbidden, haystack)
        for forbidden in ("captcha", "checkout_submit", "payment_submit", "openai", "anthropic"):
            self.assertNotIn(forbidden, haystack)

    def assert_required_keys(self, row: dict, keys: tuple[str, ...]) -> None:
        for key in keys:
            self.assertIn(key, row)
            self.assertNotEqual(row[key], "", key)


if __name__ == "__main__":
    unittest.main()
