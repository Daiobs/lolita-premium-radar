import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, diff_and_store
from lolita_radar.web import INDEX_HTML, get_dashboard_state, make_handler


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
        self.assertIn("marketForm", INDEX_HTML)
        self.assertIn("/api/market/observations", INDEX_HTML)
        self.assertIn("priorityScore", INDEX_HTML)

    def test_market_observation_post_appends_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            market_path = root / "market.json"
            config_path.write_text(
                """
sources:
  metamorphose:
    type: metamorphose
    enabled: true
    url: "https://metamorphose.gr.jp/en/news"
""".strip(),
                encoding="utf-8",
            )
            market_path.write_text("[]\n", encoding="utf-8")

            handler = make_handler(config_path=config_path, db_path=db_path, market_path=market_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/market/observations"
                request = urllib.request.Request(
                    url,
                    data=json.dumps(
                        {
                            "brand_alias": "AP",
                            "item_name": "Rose JSK",
                            "retail_price": 2000,
                            "resale_price": 3000,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(payload["added_market_observation"]["premium_rate"], 0.5)
                self.assertEqual(payload["market"]["summary"]["sample_count"], 1)
                self.assertEqual(payload["market"]["summary"]["brands"][0]["brand_alias"], "AP")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
