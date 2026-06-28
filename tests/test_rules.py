import unittest

from lolita_radar.models import ItemStatus, RadarItem, classify_title
from lolita_radar.rules import item_matches_keywords, keyword_matches


class RuleTests(unittest.TestCase):
    def test_classify_title(self) -> None:
        self.assertEqual(classify_title("Pre-order starts now"), ItemStatus.PREORDER)
        self.assertEqual(classify_title("Restock notice"), ItemStatus.RESTOCK)
        self.assertEqual(classify_title("New Arrival JSK"), ItemStatus.NEW_ARRIVAL)
        self.assertEqual(classify_title("Holiday notice"), ItemStatus.SHOP_NEWS)

    def test_keyword_matching_is_case_insensitive(self) -> None:
        item = RadarItem(
            source="generic",
            title="Public shop page",
            url="https://example.com",
            status=ItemStatus.SHOP_NEWS,
            content="Angelic Pretty JSK preorder is open.",
        )

        self.assertEqual(keyword_matches(item.content, ["jsk", "op"]), ["jsk"])
        self.assertTrue(item_matches_keywords(item, ["angelic pretty", "预约"]))
        self.assertFalse(item_matches_keywords(item, ["metamorphose"]))


if __name__ == "__main__":
    unittest.main()
