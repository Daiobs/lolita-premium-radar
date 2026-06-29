import json
import tempfile
import unittest
from pathlib import Path

from lolita_radar.brands import build_focus_queue, keyword_matches, load_brand_weights


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


if __name__ == "__main__":
    unittest.main()
