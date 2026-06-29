import unittest
from pathlib import Path

from lolita_radar.parsers import (
    parse_alice_and_the_pirates_news,
    parse_angelic_pretty_news,
    parse_baby_ssb_news,
    parse_moitie_news,
)


FIXTURES = Path("tests/fixtures")


class LiveParserFixtureTests(unittest.TestCase):
    def test_angelic_pretty_fixture_has_category_and_section(self) -> None:
        items = parse_angelic_pretty_news(
            (FIXTURES / "angelic_pretty_news.html").read_text(encoding="utf-8"),
            "https://angelicpretty.com/",
        )

        self.assertEqual(items[0].metadata["category"], "new_arrival")
        self.assertEqual(items[0].metadata["section"], "dress")

    def test_baby_fixture_keeps_brand_metadata(self) -> None:
        items = parse_baby_ssb_news(
            (FIXTURES / "baby_ssb_news.html").read_text(encoding="utf-8"),
            "https://www.babyssb.co.jp/news/",
        )

        self.assertIn("BABY", items[0].metadata["brand"])
        self.assertEqual(items[0].metadata["category"], "preorder")

    def test_aatp_fixture_filters_pirates_content(self) -> None:
        items = parse_alice_and_the_pirates_news(
            (FIXTURES / "alice_and_the_pirates_news.html").read_text(encoding="utf-8"),
            "https://www.babyssb.co.jp/news/",
        )

        self.assertEqual(len(items), 1)
        self.assertIn("PIRATES", items[0].metadata["brand"])

    def test_moitie_fixture_has_news_category(self) -> None:
        items = parse_moitie_news(
            (FIXTURES / "moitie_news.html").read_text(encoding="utf-8"),
            "https://moi-meme-moitie.com/",
        )

        self.assertEqual(items[0].metadata["category"], "new_arrival")
        self.assertIn("Moitie", items[0].metadata["brand"])


if __name__ == "__main__":
    unittest.main()
