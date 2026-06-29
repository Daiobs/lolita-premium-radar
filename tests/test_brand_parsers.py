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

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "angelic_pretty")
        self.assertEqual(items[0].title, "Shell Garden JSK")
        self.assertEqual(items[0].url, "https://angelicpretty.com/Page/Feature/NewsDetail.aspx?news=20260620")
        self.assertEqual(items[0].published_at, "2026-06-20")
        self.assertEqual(items[0].status, ItemStatus.NEW_ARRIVAL)
        self.assertEqual(items[0].metadata["brand"], "Angelic Pretty")
        self.assertEqual(items[0].metadata["category"], "new_arrival")
        self.assertEqual(items[0].metadata["section"], "dress")
        self.assertEqual(items[0].metadata["price"], "¥38,280")
        self.assert_no_navigation(items)

    def test_baby_fixture_classifies_preorder(self) -> None:
        html = (FIXTURES / "baby_ssb_news.html").read_text(encoding="utf-8")

        items = parse_baby_ssb_news(html, "https://www.babyssb.co.jp/news/")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source, "baby_ssb")
        self.assertEqual(items[0].title, "Usakumya Pochette")
        self.assertEqual(items[0].url, "https://www.babyssb.co.jp/news/20260621-reservation-usakumya/")
        self.assertEqual(items[0].published_at, "2026-06-21")
        self.assertEqual(items[0].status, ItemStatus.PREORDER)
        self.assertIn("BABY", items[0].metadata["brand"])
        self.assertEqual(items[0].metadata["category"], "preorder")
        self.assertEqual(items[0].metadata["section"], "accessory")
        self.assertEqual(items[0].metadata["price"], "12,980円")
        self.assertEqual(items[1].metadata["brand"], "ALICE and the PIRATES")
        self.assert_no_navigation(items)

    def test_aatp_fixture_classifies_restock(self) -> None:
        html = (FIXTURES / "alice_and_the_pirates_news.html").read_text(encoding="utf-8")

        items = parse_alice_and_the_pirates_news(html, "https://www.babyssb.co.jp/news/")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "alice_and_the_pirates")
        self.assertEqual(items[0].title, "Vampire Requiem JSK by ALICE and the PIRATES")
        self.assertEqual(items[0].url, "https://www.babyssb.co.jp/news/20260622-restock-vampire-requiem/")
        self.assertEqual(items[0].published_at, "2026-06-22")
        self.assertEqual(items[0].status, ItemStatus.RESTOCK)
        self.assertIn("PIRATES", items[0].metadata["brand"])
        self.assertEqual(items[0].metadata["category"], "restock")
        self.assertEqual(items[0].metadata["section"], "dress")
        self.assert_no_navigation(items)

    def test_moitie_fixture_classifies_new_arrival(self) -> None:
        html = (FIXTURES / "moitie_news.html").read_text(encoding="utf-8")

        items = parse_moitie_news(html, "https://moi-meme-moitie.com/blogs/news")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "moitie")
        self.assertEqual(items[0].title, "Iron Gate OP")
        self.assertEqual(items[0].url, "https://moi-meme-moitie.com/blogs/news/20260623-new-item-iron-gate")
        self.assertEqual(items[0].published_at, "2026-06-23")
        self.assertEqual(items[0].status, ItemStatus.NEW_ARRIVAL)
        self.assertIn("Moitie", items[0].metadata["brand"])
        self.assertEqual(items[0].metadata["category"], "new_arrival")
        self.assertEqual(items[0].metadata["section"], "dress")
        self.assertEqual(items[0].metadata["price"], "￥49,500")
        self.assert_no_navigation(items)

    def assert_no_navigation(self, items) -> None:
        haystack = " ".join(f"{item.title} {item.url}" for item in items).lower()
        for token in ("login", "cart", "privacy", "contact"):
            self.assertNotIn(token, haystack)


if __name__ == "__main__":
    unittest.main()
