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
        self.assertEqual(items[0].url, "https://angelicpretty.com/Page/Feature/NewsDetail.aspx?news=shell-garden")
        self.assertEqual(items[0].published_at, "2026-06-20")
        self.assertEqual(items[0].status, ItemStatus.NEW_ARRIVAL)
        self.assertEqual(items[0].metadata["brand"], "Angelic Pretty")
        self.assertEqual(items[0].metadata["category"], "new_arrival")
        self.assertEqual(items[0].metadata["section"], "dress")
        self.assertEqual(items[0].metadata["price"], "¥38,280")
        self.assertEqual(items[0].metadata["image_url"], "https://angelicpretty.com/Contents/Feature/shell-garden.webp")
        self.assert_no_navigation(items)

    def test_angelic_pretty_extracts_compact_date_from_url(self) -> None:
        html = """
        <article>
          <span class="category">ご予約</span>
          <a href="/Page/Feature/20260627.aspx">恋するお姫様ジャンパースカートSetご予約会のご案内</a>
        </article>
        """

        items = parse_angelic_pretty_news(html, "https://angelicpretty.com/Page/news/")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].published_at, "2026-06-27")
        self.assertEqual(items[0].status, ItemStatus.PREORDER)

    def test_angelic_pretty_replaces_image_path_titles(self) -> None:
        html = """
        <article>
          <span>ご予約</span>
          <a href="/Page/Feature/20260627.aspx">
            <img alt="/Contents%2fFeature%2f2026pre02_s.jpg の画像">
          </a>
        </article>
        """

        items = parse_angelic_pretty_news(html, "https://angelicpretty.com/Page/news/")

        self.assertEqual(items[0].title, "Angelic Pretty 2026-06-27 予約特集 / 预约特集")
        self.assertEqual(items[0].published_at, "2026-06-27")

    def test_baby_fixture_classifies_preorder(self) -> None:
        html = (FIXTURES / "baby_ssb_news.html").read_text(encoding="utf-8")

        items = parse_baby_ssb_news(html, "https://www.babyssb.co.jp/news/")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source, "baby_ssb")
        self.assertEqual(items[0].title, "Usakumya Pochette")
        self.assertEqual(items[0].url, "https://www.babyssb.co.jp/news/reservation-usakumya/")
        self.assertEqual(items[0].published_at, "2026-06-21")
        self.assertEqual(items[0].status, ItemStatus.PREORDER)
        self.assertIn("BABY", items[0].metadata["brand"])
        self.assertEqual(items[0].metadata["category"], "preorder")
        self.assertEqual(items[0].metadata["section"], "accessory")
        self.assertEqual(items[0].metadata["price"], "12,980円")
        self.assertEqual(items[0].metadata["image_url"], "https://www.babyssb.co.jp/uploads/usakumya.webp")
        self.assertEqual(items[1].metadata["brand"], "ALICE and the PIRATES")
        self.assertEqual(items[1].published_at, "2026-06-22")
        self.assertEqual(items[1].metadata["category"], "event")
        self.assert_no_navigation(items)

    def test_aatp_fixture_classifies_restock(self) -> None:
        html = (FIXTURES / "alice_and_the_pirates_news.html").read_text(encoding="utf-8")

        items = parse_alice_and_the_pirates_news(html, "https://www.babyssb.co.jp/news/")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "alice_and_the_pirates")
        self.assertEqual(items[0].title, "Vampire Requiem JSK by ALICE and the PIRATES")
        self.assertEqual(items[0].url, "https://www.babyssb.co.jp/news/restock-vampire-requiem/")
        self.assertEqual(items[0].published_at, "2026-06-22")
        self.assertEqual(items[0].status, ItemStatus.RESTOCK)
        self.assertIn("PIRATES", items[0].metadata["brand"])
        self.assertEqual(items[0].metadata["category"], "restock")
        self.assertEqual(items[0].metadata["section"], "dress")
        self.assertEqual(items[0].metadata["image_url"], "https://www.babyssb.co.jp/uploads/vampire-requiem.webp")
        self.assert_no_navigation(items)

    def test_moitie_fixture_classifies_new_arrival(self) -> None:
        html = (FIXTURES / "moitie_news.html").read_text(encoding="utf-8")

        items = parse_moitie_news(html, "https://moi-meme-moitie.com/blogs/news")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "moitie")
        self.assertEqual(items[0].title, "Iron Gate OP")
        self.assertEqual(items[0].url, "https://moi-meme-moitie.com/blogs/news/new-item-iron-gate")
        self.assertEqual(items[0].published_at, "2026-06-23")
        self.assertEqual(items[0].status, ItemStatus.NEW_ARRIVAL)
        self.assertIn("Moitie", items[0].metadata["brand"])
        self.assertEqual(items[0].metadata["category"], "new_arrival")
        self.assertEqual(items[0].metadata["section"], "dress")
        self.assertEqual(items[0].metadata["price"], "￥49,500")
        self.assertEqual(items[0].metadata["image_url"], "https://moi-meme-moitie.com/cdn/shop/files/iron-gate.webp")
        self.assert_no_navigation(items)

    def assert_no_navigation(self, items) -> None:
        haystack = " ".join(f"{item.title} {item.url}" for item in items).lower()
        for token in ("login", "cart", "privacy", "contact"):
            self.assertNotIn(token, haystack)


if __name__ == "__main__":
    unittest.main()
