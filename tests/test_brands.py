import json
import tempfile
import unittest
from pathlib import Path

from lolita_radar.brands import build_watch_signals, keyword_matches, load_brand_weights, save_brand_weights


class BrandTests(unittest.TestCase):
    def test_load_brand_weights_sorts_and_normalizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "brands.json"
            path.write_text(
                json.dumps(
                    [
                        {"name": "Meta", "alias": "Meta", "weight": 80, "keywords": ["metamorphose"]},
                        {
                            "name": "Angelic Pretty",
                            "alias": "AP",
                            "weight": 100,
                            "keywords": ["angelic pretty"],
                            "market_keywords": ["贝壳", "Holy Lantern"],
                            "watch_urls": [
                                {"label": "闲鱼", "url": "https://www.goofish.com/search?q=Angelic+Pretty+lolita"},
                                {"label": "bad", "url": "javascript:alert(1)"},
                                {"label": "闲鱼", "url": "https://www.goofish.com/search?q=Angelic+Pretty+lolita"},
                            ],
                            "visual": {
                                "accent": "#b4576f",
                                "paper": "#fff3f6",
                                "motif": "ribbon",
                                "radar_cue": "甜系印花优先",
                            },
                        },
                    ]
                ),
                encoding="utf-8",
            )

            brands = load_brand_weights(path)

            self.assertEqual([brand["alias"] for brand in brands], ["AP", "Meta"])
            self.assertIn("ap", brands[0]["keywords"])
            self.assertEqual(brands[0]["market_keywords"], ["贝壳", "Holy Lantern"])
            self.assertEqual(
                brands[0]["watch_urls"],
                [{"label": "闲鱼", "url": "https://www.goofish.com/search?q=Angelic+Pretty+lolita"}],
            )
            self.assertEqual(brands[0]["visual"]["accent"], "#b4576f")
            self.assertEqual(brands[0]["visual"]["motif"], "ribbon")
            self.assertEqual(brands[1]["watch_urls"][0]["label"], "闲鱼")

    def test_save_brand_weights_updates_existing_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "brands.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "name": "Meta",
                            "alias": "Meta",
                            "weight": 80,
                            "tier": "watch",
                            "keywords": ["metamorphose"],
                            "watch_urls": [{"label": "Mercari", "url": "https://jp.mercari.com/search?keyword=Meta+lolita"}],
                            "visual": {"accent": "#0f6760", "paper": "#f1fbf8", "motif": "swan"},
                        },
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
            self.assertEqual(meta["watch_urls"][0]["label"], "Mercari")
            self.assertEqual(meta["visual"]["accent"], "#0f6760")
            self.assertEqual(meta["visual"]["motif"], "swan")
            saved = load_brand_weights(path)
            self.assertEqual(next(brand for brand in saved if brand["alias"] == "Meta")["weight"], 94)

    def test_default_weights_include_market_keywords(self) -> None:
        brands = load_brand_weights()
        ap = next(brand for brand in brands if brand["alias"] == "AP")

        self.assertIn("贝壳", ap["market_keywords"])
        self.assertIn("goofish.com", ap["watch_urls"][0]["url"])
        self.assertEqual(ap["visual"]["motif"], "ribbon / shell print")
        self.assertEqual(ap["visual"]["accent"], "#b4576f")

    def test_short_alias_requires_word_boundary(self) -> None:
        self.assertFalse(keyword_matches("ap", "new arrival in april"))
        self.assertFalse(keyword_matches("ap", "public-shop-page"))
        self.assertTrue(keyword_matches("ap", "ap special set"))

    def test_watch_signals_uses_observed_brand_matches(self) -> None:
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

        signals = build_watch_signals(brands, items, events)

        self.assertIn("Meta", [brand["alias"] for brand in signals])
        meta = next(brand for brand in signals if brand["alias"] == "Meta")
        self.assertEqual(meta["item_count"], 1)
        self.assertEqual(meta["event_count"], 1)

    def test_watch_signals_uses_market_premium(self) -> None:
        brands = load_brand_weights()
        market_brands = [{"brand_alias": "BABY", "sample_count": 2, "avg_premium_rate": 0.4}]

        signals = build_watch_signals(brands, items=[], events=[], market_brands=market_brands)

        baby = next(brand for brand in signals if brand["alias"] == "BABY")
        self.assertEqual(baby["market_count"], 2)
        self.assertEqual(baby["avg_premium_rate"], 0.4)
        self.assertEqual(baby["score"], 100)


if __name__ == "__main__":
    unittest.main()
