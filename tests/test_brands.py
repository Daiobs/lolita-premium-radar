import json
import tempfile
import unittest
from pathlib import Path

from lolita_radar.brands import build_focus_queue, keyword_matches, load_brand_weights, save_brand_weights


class BrandTests(unittest.TestCase):
    def test_load_brand_weights_sorts_and_normalizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "brands.json"
            path.write_text(
                json.dumps(
                    [
                        {"name": "Meta", "alias": "Meta", "weight": 80, "keywords": ["metamorphose"]},
                        {"name": "Angelic Pretty", "alias": "AP", "weight": 100, "keywords": ["angelic pretty"]},
                    ]
                ),
                encoding="utf-8",
            )

            brands = load_brand_weights(path)

            self.assertEqual([brand["alias"] for brand in brands], ["AP", "Meta"])
            self.assertIn("ap", brands[0]["keywords"])

    def test_save_brand_weights_updates_existing_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "brands.json"
            path.write_text(
                json.dumps(
                    [
                        {"name": "Meta", "alias": "Meta", "weight": 80, "tier": "watch", "keywords": ["metamorphose"]},
                        {"name": "Angelic Pretty", "alias": "AP", "weight": 100, "tier": "core", "keywords": ["angelic pretty"]},
                    ]
                ),
                encoding="utf-8",
            )

            brands = save_brand_weights(path, [{"alias": "Meta", "weight": 94}])

            meta = next(brand for brand in brands if brand["alias"] == "Meta")
            self.assertEqual(meta["weight"], 94)
            self.assertEqual(meta["tier"], "watch")
            self.assertIn("metamorphose", meta["keywords"])
            saved = load_brand_weights(path)
            self.assertEqual(next(brand for brand in saved if brand["alias"] == "Meta")["weight"], 94)

    def test_short_alias_requires_word_boundary(self) -> None:
        self.assertFalse(keyword_matches("ap", "new arrival in april"))
        self.assertFalse(keyword_matches("ap", "public-shop-page"))
        self.assertTrue(keyword_matches("ap", "ap special set"))

    def test_focus_queue_uses_observed_brand_matches(self) -> None:
        brands = load_brand_weights()
        items = [
            {
                "source": "metamorphose",
                "title": "News New Arrival JSK",
                "url": "https://metamorphose.gr.jp/en/metamornews/1",
                "status": "new_arrival",
            }
        ]
        events = [
            {
                "source": "metamorphose",
                "title": "News New Arrival JSK",
                "url": "https://metamorphose.gr.jp/en/metamornews/1",
                "status": "new_arrival",
            }
        ]

        queue = build_focus_queue(brands, items, events)

        self.assertIn("Meta", [brand["alias"] for brand in queue])
        meta = next(brand for brand in queue if brand["alias"] == "Meta")
        self.assertEqual(meta["item_count"], 1)
        self.assertEqual(meta["event_count"], 1)

    def test_focus_queue_uses_market_premium(self) -> None:
        brands = load_brand_weights()
        market_brands = [{"brand_alias": "BABY", "sample_count": 2, "avg_premium_rate": 0.4}]

        queue = build_focus_queue(brands, items=[], events=[], market_brands=market_brands)

        baby = next(brand for brand in queue if brand["alias"] == "BABY")
        self.assertEqual(baby["market_count"], 2)
        self.assertEqual(baby["avg_premium_rate"], 0.4)
        self.assertEqual(baby["score"], 100)


if __name__ == "__main__":
    unittest.main()
