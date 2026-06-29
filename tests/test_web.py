import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, diff_and_store
from lolita_radar.web import FEED_INDEX_HTML, INDEX_HTML, get_dashboard_state, make_handler


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
            self.assertIn("patterns", state["market"])
            self.assertIn("momentum", state["market"])
            self.assertIn("sample_plan", state["market"])
            self.assertTrue(state["market"]["sample_plan"])
            self.assertIn("premium_bands", state["market"]["summary"])
            self.assertIn("opportunity_radar", state)
            self.assertIn("brand_weight_profile", state)
            self.assertIn("market_alerts", state)
            self.assertIn("summary", state["market_alerts"])
            self.assertTrue(state["market_alerts"]["alerts"])
            self.assertEqual(state["brand_weight_profile"][0]["alias"], "AP")
            self.assertIn("weight_role", state["brand_weight_profile"][0])
            self.assertIn("watch_urls", state["brand_weight_profile"][0])
            self.assertTrue(state["opportunity_radar"])
            self.assertTrue(state["focus_queue"])
            self.assertIn("feed", state)
            self.assertIn("release", state["feed"]["streams"])
            self.assertIn("drop", state["feed"]["streams"])
            self.assertIn("trend", state["feed"]["streams"])
            self.assertIn("alert", state["feed"]["streams"])
            self.assertEqual(state["feed"]["summary"]["drops"], 1)
            self.assertEqual(state["items"][0]["title"], "New Arrival: Rose JSK")
            self.assertEqual(state["events"][0]["event_type"], "new_item")

    def test_feed_index_html_is_feed_app(self) -> None:
        self.assertIn("Lolita Radar OS", FEED_INDEX_HTML)
        self.assertIn("feedStream", FEED_INDEX_HTML)
        self.assertIn("data-filter=\"release\"", FEED_INDEX_HTML)
        self.assertIn("data-filter=\"drop\"", FEED_INDEX_HTML)
        self.assertIn("data-filter=\"trend\"", FEED_INDEX_HTML)
        self.assertIn("data-filter=\"alert\"", FEED_INDEX_HTML)
        self.assertIn("🔥 Drops", FEED_INDEX_HTML)
        self.assertNotIn("northStarRadar", FEED_INDEX_HTML)
        self.assertNotIn("brandCrownQueue", FEED_INDEX_HTML)

    def test_index_html_is_feed_app_alias(self) -> None:
        self.assertEqual(INDEX_HTML, FEED_INDEX_HTML)
        self.assertIn("Lolita Radar OS", INDEX_HTML)
        self.assertIn("feedStream", INDEX_HTML)
        self.assertIn('data-filter="release"', INDEX_HTML)
        self.assertIn('data-filter="drop"', INDEX_HTML)
        self.assertIn('data-filter="trend"', INDEX_HTML)
        self.assertIn('data-filter="alert"', INDEX_HTML)
        self.assertNotIn("northStarRadar", INDEX_HTML)
        self.assertNotIn("brandCrownQueue", INDEX_HTML)
        self.assertNotIn("brandRadarMatrix", INDEX_HTML)

    def test_static_visual_asset_is_served(self) -> None:
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
""".strip(),
                encoding="utf-8",
            )

            handler = make_handler(config_path=config_path, db_path=db_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/assets/lolita-radar-fabric.png"
                with urllib.request.urlopen(url) as response:
                    body = response.read(8)

                self.assertEqual(response.headers.get_content_type(), "image/png")
                self.assertEqual(body, b"\x89PNG\r\n\x1a\n")
            finally:
                server.shutdown()
                server.server_close()

    def test_root_serves_feed_index(self) -> None:
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
""".strip(),
                encoding="utf-8",
            )

            handler = make_handler(config_path=config_path, db_path=db_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/"
                with urllib.request.urlopen(url) as response:
                    body = response.read().decode("utf-8")

                self.assertIn("Lolita Radar OS", body)
                self.assertIn("feedStream", body)
                self.assertNotIn("northStarRadar", body)
            finally:
                server.shutdown()
                server.server_close()

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

    def test_brand_weights_put_updates_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            brands_path = root / "brands.json"
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
            brands_path.write_text(
                json.dumps(
                    [
                        {"name": "Angelic Pretty", "alias": "AP", "weight": 100, "tier": "core", "keywords": ["angelic pretty"]},
                        {"name": "Metamorphose", "alias": "Meta", "weight": 80, "tier": "watch", "keywords": ["metamorphose"]},
                    ]
                ),
                encoding="utf-8",
            )

            handler = make_handler(config_path=config_path, db_path=db_path, brands_path=brands_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/brand-weights"
                request = urllib.request.Request(
                    url,
                    data=json.dumps({"weights": [{"alias": "Meta", "weight": 96}]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urllib.request.urlopen(request) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                meta = next(brand for brand in payload["brand_weights"] if brand["alias"] == "Meta")
                self.assertEqual(meta["weight"], 96)
                saved = json.loads(brands_path.read_text(encoding="utf-8"))
                self.assertEqual(next(brand for brand in saved if brand["alias"] == "Meta")["weight"], 96)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
