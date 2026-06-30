import unittest
from pathlib import Path

from lolita_radar.models import ItemStatus
from lolita_radar.parsers import extract_date, parse_generic_text, parse_metamorphose_news


class ParserTests(unittest.TestCase):
    def test_parse_metamorphose_news_extracts_release_items(self) -> None:
        html = Path("tests/fixtures/metamorphose_news.html").read_text(encoding="utf-8")

        items = parse_metamorphose_news(html, "https://metamorphose.gr.jp/en/news")

        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].title, "New Arrival: Rose Ribbon JSK")
        self.assertEqual(items[0].url, "https://metamorphose.gr.jp/en/news/2026-06-28-new-arrival")
        self.assertEqual(items[0].published_at, "2026-06-28")
        self.assertEqual(items[0].status, ItemStatus.NEW_ARRIVAL)
        self.assertEqual(items[1].status, ItemStatus.PREORDER)
        self.assertEqual(items[2].status, ItemStatus.RESTOCK)

    def test_parse_generic_text_extracts_visible_text(self) -> None:
        text = parse_generic_text("<html><body><h1>Shop</h1><p>New JSK preorder open.</p></body></html>")

        self.assertIn("Shop", text)
        self.assertIn("preorder", text)

    def test_extract_date_accepts_japanese_year_month_day_source_time(self) -> None:
        self.assertEqual(extract_date("掲載日：2026年6月30日 新作入荷"), "2026-06-30")


if __name__ == "__main__":
    unittest.main()
