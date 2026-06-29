import unittest
from pathlib import Path

from lolita_radar.models import ItemStatus
from lolita_radar.parsers import (
    parse_alice_and_the_pirates_news,
    parse_angelic_pretty_news,
    parse_baby_ssb_news,
    parse_moitie_news,
)


FIXTURES = Path("tests/fixtures")


class BrandParserTests(unittest.TestCase):
    def test_angelic_pretty_fixture_classifies_new_arrival(self) -> None:
        html = (FIXTURES / "angelic_pretty_news.html").read_text(encoding="utf-8")

        items = parse_angelic_pretty_news(html, "https://angelicpretty.com/Page/Feature/News.aspx")

        self.assertEqual(items[0].source, "angelic_pretty")
        self.assertEqual(items[0].status, ItemStatus.NEW_ARRIVAL)
        self.assertEqual(items[0].metadata["brand"], "Angelic Pretty")

    def test_baby_fixture_classifies_preorder(self) -> None:
        html = (FIXTURES / "baby_ssb_news.html").read_text(encoding="utf-8")

        items = parse_baby_ssb_news(html, "https://www.babyssb.co.jp/news/")

        self.assertEqual(items[0].source, "baby_ssb")
        self.assertEqual(items[0].status, ItemStatus.PREORDER)
        self.assertIn("BABY", items[0].metadata["brand"])

    def test_aatp_fixture_classifies_restock(self) -> None:
        html = (FIXTURES / "alice_and_the_pirates_news.html").read_text(encoding="utf-8")

        items = parse_alice_and_the_pirates_news(html, "https://www.babyssb.co.jp/news/")

        self.assertEqual(items[0].source, "alice_and_the_pirates")
        self.assertEqual(items[0].status, ItemStatus.RESTOCK)
        self.assertIn("PIRATES", items[0].metadata["brand"])

    def test_moitie_fixture_classifies_new_arrival(self) -> None:
        html = (FIXTURES / "moitie_news.html").read_text(encoding="utf-8")

        items = parse_moitie_news(html, "https://moi-meme-moitie.com/blogs/news")

        self.assertEqual(items[0].source, "moitie")
        self.assertEqual(items[0].status, ItemStatus.NEW_ARRIVAL)
        self.assertIn("Moitie", items[0].metadata["brand"])


if __name__ == "__main__":
    unittest.main()
