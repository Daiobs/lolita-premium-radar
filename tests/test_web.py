import tempfile
import unittest
from pathlib import Path

from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, diff_and_store
from lolita_radar.web import INDEX_HTML, get_dashboard_state


class WebTests(unittest.TestCase):
    def test_dashboard_state_includes_sources_items_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  metamorphose:
    type: metamorphose
    enabled: true
    url: "https://metamorphose.gr.jp/en/news"
    keywords:
      - "JSK"
      - "OP"
""".strip(),
                encoding="utf-8",
            )

            connection = connect(db_path)
            try:
                diff_and_store(
                    connection,
                    [
                        RadarItem(
                            source="metamorphose",
                            title="New Arrival: Rose JSK",
                            url="https://example.com/news/rose",
                            status=ItemStatus.NEW_ARRIVAL,
                        )
                    ],
                )
            finally:
                connection.close()

            state = get_dashboard_state(config_path=config_path, db_path=db_path)

            self.assertTrue(state["ok"])
            self.assertEqual(state["counts"]["sources"], 1)
            self.assertEqual(state["counts"]["items"], 1)
            self.assertEqual(state["counts"]["events"], 1)
            self.assertEqual(state["sources"][0]["name"], "metamorphose")
            self.assertEqual(state["brand_weights"][0]["alias"], "AP")
            self.assertEqual(state["brand_weights"][0]["weight"], 100)
            self.assertIn("brands_path", state)
            self.assertIn("market_path", state)
            self.assertIn("market", state)
            self.assertTrue(state["focus_queue"])
            self.assertEqual(state["items"][0]["title"], "New Arrival: Rose JSK")
            self.assertEqual(state["events"][0]["event_type"], "new_item")

    def test_index_html_includes_language_switch(self) -> None:
        self.assertIn('data-language="zh"', INDEX_HTML)
        self.assertIn('data-language="en"', INDEX_HTML)
        self.assertIn("中文", INDEX_HTML)
        self.assertIn("Check All", INDEX_HTML)
        self.assertIn("brandWeights", INDEX_HTML)
        self.assertIn("marketSignal", INDEX_HTML)
        self.assertIn("focusQueue", INDEX_HTML)
        self.assertIn("marketPremium", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
