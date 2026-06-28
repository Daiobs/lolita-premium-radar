import unittest
from pathlib import Path

from tb_new_arrival_alert.extractors import extract_items
from tb_new_arrival_alert.matcher import matches_target
from tb_new_arrival_alert.models import Target


class ExtractorTests(unittest.TestCase):
    def test_extracts_taobao_and_tmall_items(self) -> None:
        html = Path("tests/fixtures/sample_shop.html").read_text(encoding="utf-8")
        items = extract_items(html, "https://shop.example.taobao.com/search.htm")

        self.assertEqual({item.item_id for item in items}, {"100000000001", "100000000002", "100000000003"})
        self.assertIn("https://item.taobao.com/item.htm?id=100000000001", {item.url for item in items})
        self.assertIn("https://detail.tmall.com/item.htm?id=100000000002", {item.url for item in items})

    def test_keyword_and_price_matching(self) -> None:
        html = Path("tests/fixtures/sample_shop.html").read_text(encoding="utf-8")
        items = extract_items(html, "https://shop.example.taobao.com/search.htm")
        target = Target(
            name="sample",
            url="https://shop.example.taobao.com/search.htm",
            enabled=True,
            include_keywords=("JSK", "OP"),
            exclude_keywords=("尾款",),
            price_min=None,
            price_max=500,
        )

        matched = [item.item_id for item in items if matches_target(item, target)]

        self.assertEqual(matched, ["100000000001"])


if __name__ == "__main__":
    unittest.main()
