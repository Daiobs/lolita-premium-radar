import unittest

from lolita_radar.shop import build_drop_signal


class ShopModelTests(unittest.TestCase):
    def test_build_drop_signal_maps_shop_item_keywords_and_urgency(self) -> None:
        row = {
            "source": "generic_page",
            "event_type": "content_changed",
            "title": "Public shop page",
            "url": "https://example.com/shop",
            "metadata": {
                "shop": {"name": "Tokyo Proxy", "url": "https://example.com/shop"},
                "item": {"title": "Shell Garden JSK", "url": "https://example.com/shop/shell"},
                "matched_keywords": ["JSK", "预约", "noise"],
            },
        }

        signal = build_drop_signal(row)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.shop.name, "Tokyo Proxy")
        self.assertEqual(signal.item.title, "Shell Garden JSK")
        self.assertEqual(signal.item.url, "https://example.com/shop/shell")
        self.assertEqual(signal.item.keywords, ("JSK", "预约"))
        self.assertEqual(signal.urgency, "high")
        self.assertEqual(signal.reason_codes[:3], ("shop_item_changed", "keyword_match", "kw:JSK"))

    def test_build_drop_signal_ignores_non_drop_keywords(self) -> None:
        signal = build_drop_signal(
            {
                "source": "generic_page",
                "event_type": "content_changed",
                "title": "Plain page",
                "url": "https://example.com/shop",
                "metadata": {"matched_keywords": ["brand"]},
            }
        )

        self.assertIsNone(signal)

    def test_build_drop_signal_accepts_new_structured_shop_item_without_keywords(self) -> None:
        signal = build_drop_signal(
            {
                "source": "generic_page",
                "event_type": "new_item",
                "title": "Public shop page",
                "url": "https://example.com/shop",
                "metadata": {
                    "shop": {"name": "Tokyo Proxy"},
                    "item": {"title": "Ribbon OP", "url": "https://example.com/shop/ribbon-op"},
                    "matched_keywords": [],
                },
            }
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.shop.name, "Tokyo Proxy")
        self.assertEqual(signal.item.title, "Ribbon OP")
        self.assertEqual(signal.item.keywords, ())
        self.assertEqual(signal.urgency, "high")
        self.assertEqual(signal.reason_codes, ("new_shop_item",))


if __name__ == "__main__":
    unittest.main()
