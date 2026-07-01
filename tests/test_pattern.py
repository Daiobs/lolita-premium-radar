import unittest

from lolita_radar.pattern import normalize_brand, normalize_pattern, pattern_identity


class PatternTests(unittest.TestCase):
    def test_normalizes_common_brand_aliases(self) -> None:
        self.assertEqual(normalize_brand("", "アンプリ Shell Garden JSK"), "AP")
        self.assertEqual(normalize_brand("Metamorphose temps de fille"), "Meta")
        self.assertEqual(normalize_brand("", "ALICE and the PIRATES Treasure OP"), "AATP")

    def test_normalizes_used_listing_title_to_pattern_name(self) -> None:
        title = "Moi-meme-Moitie / アイアンゲートジャンパースカート 白Ｘ黒 I-26-06-25-020-MO-OP-HD-ZI"

        identity = pattern_identity(title)

        self.assertEqual(identity.brand_alias, "MMM")
        self.assertEqual(identity.pattern, "アイアンゲートジャンパースカート")
        self.assertEqual(identity.key, "mmm|アイアンゲートジャンパースカート")

    def test_pattern_prefers_product_name_over_color_and_used_noise(self) -> None:
        self.assertEqual(normalize_pattern("[USED] Angelic Pretty Shell Garden JSK pink free", "AP"), "Shell Garden JSK")


if __name__ == "__main__":
    unittest.main()
