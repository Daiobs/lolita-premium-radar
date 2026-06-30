import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import lolita_radar.runner as runner
from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, diff_and_store, record_source_run
from lolita_radar.web import FEED_INDEX_HTML, INDEX_HTML, get_feed_payload, get_feed_state, make_handler


class FakeGoodAdapter:
    def __init__(self, config) -> None:
        self.config = config

    def fetch_items(self) -> list[RadarItem]:
        return [
            RadarItem(
                source=self.config.name,
                title="New Arrival: Test JSK",
                url=f"{self.config.url}/new",
                status=ItemStatus.NEW_ARRIVAL,
                content="fixture content",
                metadata={"brand": "Test Brand", "price": "¥12,000"},
            )
        ]


class FakeBadAdapter:
    def __init__(self, config) -> None:
        self.config = config

    def fetch_items(self) -> list[RadarItem]:
        raise RuntimeError("adapter boom")


class WebTests(unittest.TestCase):
    def test_feed_state_includes_sources_items_and_events(self) -> None:
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
                            published_at="2026-06-30",
                        )
                    ],
                )
            finally:
                connection.close()

            state = get_feed_state(config_path=config_path, db_path=db_path)

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
            self.assertEqual(state["feed"]["summary"]["releases"], 1)
            self.assertEqual(state["items"][0]["title"], "New Arrival: Rose JSK")
            self.assertEqual(state["events"][0]["event_type"], "new_item")

    def test_feed_index_html_is_feed_app(self) -> None:
        self.assertIn("Lolita Radar OS", FEED_INDEX_HTML)
        self.assertIn("feedStream", FEED_INDEX_HTML)
        self.assertIn("data-filter=\"release\"", FEED_INDEX_HTML)
        self.assertIn("data-filter=\"drop\"", FEED_INDEX_HTML)
        self.assertIn("data-filter=\"trend\"", FEED_INDEX_HTML)
        self.assertIn("data-filter=\"alert\"", FEED_INDEX_HTML)
        self.assertIn("releasesCount", FEED_INDEX_HTML)
        self.assertIn("dropsCount", FEED_INDEX_HTML)
        self.assertIn("发售", FEED_INDEX_HTML)
        self.assertIn("入荷", FEED_INDEX_HTML)
        self.assertIn("reasonHtml(row.reason_codes)", FEED_INDEX_HTML)
        self.assertIn("KIND_TEXT", FEED_INDEX_HTML)
        self.assertIn("kindLabel(row)", FEED_INDEX_HTML)
        self.assertIn("上升", FEED_INDEX_HTML)
        self.assertIn("発売情報", FEED_INDEX_HTML)
        self.assertIn("REASON_TEXT", FEED_INDEX_HTML)
        self.assertIn("reasonLabel", FEED_INDEX_HTML)
        self.assertIn("关键词命中", FEED_INDEX_HTML)
        self.assertIn("キーワード一致", FEED_INDEX_HTML)
        self.assertIn("样本溢价", FEED_INDEX_HTML)
        self.assertIn("ブランド注目", FEED_INDEX_HTML)
        self.assertIn('raw.startsWith("kw:")', FEED_INDEX_HTML)
        self.assertIn("class=\"reasons\"", FEED_INDEX_HTML)
        self.assertIn("class=\"visual", FEED_INDEX_HTML)
        self.assertIn("visual.image_url", FEED_INDEX_HTML)
        self.assertIn('loading="lazy"', FEED_INDEX_HTML)
        self.assertIn("has-image", FEED_INDEX_HTML)
        self.assertIn("titleAltHtml(row)", FEED_INDEX_HTML)
        self.assertIn("detailHtml(row)", FEED_INDEX_HTML)
        self.assertIn("metaHtml(row, text)", FEED_INDEX_HTML)
        self.assertIn("row.price", FEED_INDEX_HTML)
        self.assertIn("row.urgency", FEED_INDEX_HTML)
        self.assertIn("row.keywords", FEED_INDEX_HTML)
        self.assertIn("关键词", FEED_INDEX_HTML)
        self.assertIn("キーワード", FEED_INDEX_HTML)
        self.assertIn("row.sample_count", FEED_INDEX_HTML)
        self.assertIn("sampleCount", FEED_INDEX_HTML)
        self.assertIn("サンプル", FEED_INDEX_HTML)
        self.assertIn("row.premium_rate", FEED_INDEX_HTML)
        self.assertIn("premiumRate", FEED_INDEX_HTML)
        self.assertIn("溢价", FEED_INDEX_HTML)
        self.assertIn("プレミア", FEED_INDEX_HTML)
        self.assertIn("row.error_rate", FEED_INDEX_HTML)
        self.assertIn("errorRate", FEED_INDEX_HTML)
        self.assertIn("错误率", FEED_INDEX_HTML)
        self.assertIn("エラー率", FEED_INDEX_HTML)
        self.assertIn("row.latency_ms", FEED_INDEX_HTML)
        self.assertIn("latency", FEED_INDEX_HTML)
        self.assertIn("延迟", FEED_INDEX_HTML)
        self.assertIn("遅延", FEED_INDEX_HTML)
        self.assertIn("row.item_count", FEED_INDEX_HTML)
        self.assertIn("itemCount", FEED_INDEX_HTML)
        self.assertIn("条目", FEED_INDEX_HTML)
        self.assertIn("件数", FEED_INDEX_HTML)
        self.assertIn("STATUS_TEXT", FEED_INDEX_HTML)
        self.assertIn("statusLabel(row.status)", FEED_INDEX_HTML)
        self.assertIn("店铺资讯", FEED_INDEX_HTML)
        self.assertIn("ショップ情報", FEED_INDEX_HTML)
        self.assertIn("URGENCY_TEXT", FEED_INDEX_HTML)
        self.assertIn("urgencyLabel(row.urgency)", FEED_INDEX_HTML)
        self.assertIn("row.price_delta", FEED_INDEX_HTML)
        self.assertIn("formatPercent(row.price_delta)", FEED_INDEX_HTML)
        self.assertIn("timeLabel(row)", FEED_INDEX_HTML)
        self.assertIn("langBtn", FEED_INDEX_HTML)
        self.assertIn("ロリィタ発売情報", FEED_INDEX_HTML)
        self.assertIn('const tag = hasUrl ? "a" : "article"', FEED_INDEX_HTML)
        self.assertIn("打开来源", FEED_INDEX_HTML)
        self.assertIn("ソースを開く", FEED_INDEX_HTML)
        self.assertIn("暂无来源链接", FEED_INDEX_HTML)
        self.assertIn("FEED_LABELS", FEED_INDEX_HTML)
        self.assertIn("FEED_LABELS[language][activeFilter]", FEED_INDEX_HTML)
        self.assertIn('api("/api/feed")', FEED_INDEX_HTML)
        self.assertNotIn('href="${escapeHtml(href)}"', FEED_INDEX_HTML)
        self.assertNotIn("northStarRadar", FEED_INDEX_HTML)
        self.assertNotIn("brandCrownQueue", FEED_INDEX_HTML)

    def test_feed_index_html_keeps_dashboard_concepts_out(self) -> None:
        lowered = FEED_INDEX_HTML.lower()

        for forbidden in ("dashboard", "northstar", "north star", "matrix", "salon", "brandcrown"):
            self.assertNotIn(forbidden, lowered)

    def test_feed_state_source_health_alert_uses_configured_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  angelic_pretty:
    type: angelic_pretty
    enabled: true
    url: "https://angelicpretty.com/Page/news/"
""".strip(),
                encoding="utf-8",
            )
            connection = connect(db_path)
            try:
                record_source_run(
                    connection,
                    "angelic_pretty",
                    ok=False,
                    status="failed",
                    error_rate=1.0,
                    error_message="timeout",
                )
                connection.commit()
            finally:
                connection.close()

            state = get_feed_state(config_path=config_path, db_path=db_path)
            source_alerts = [
                alert for alert in state["feed"]["streams"]["alert"] if alert.get("reason_codes") == ["source_health"]
            ]

            self.assertEqual(len(source_alerts), 1)
            self.assertEqual(source_alerts[0]["url"], "https://angelicpretty.com/Page/news/")
            self.assertEqual(source_alerts[0]["brand"], "Angelic Pretty")
            self.assertEqual(source_alerts[0]["title"], "Angelic Pretty source unavailable")
            self.assertNotIn("angelic_pretty", source_alerts[0]["title"])

    def test_feed_state_outputs_feed_os_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            market_path = root / "market.json"
            config_path.write_text(
                """
sources:
  angelic_pretty:
    type: angelic_pretty
    enabled: true
    url: "https://example.com/ap"
  proxy:
    type: generic_page
    enabled: true
    url: "https://example.com/proxy"
    keywords:
      - "JSK"
""".strip(),
                encoding="utf-8",
            )
            market_path.write_text(
                """
[
  {
    "brand_alias": "AP",
    "item_name": "Shell Garden JSK",
    "retail_price": 2000,
    "resale_price": 3000,
    "observed_at": "2026-06-30"
  },
  {
    "brand_alias": "AP",
    "item_name": "Shell Garden OP",
    "retail_price": 1800,
    "resale_price": 2700,
    "observed_at": "2026-06-30"
  }
]
""".strip(),
                encoding="utf-8",
            )
            connection = connect(db_path)
            try:
                diff_and_store(
                    connection,
                    [
                        RadarItem(
                            source="angelic_pretty",
                            title="Shell Garden JSK",
                            url="https://example.com/ap/shell",
                            status=ItemStatus.NEW_ARRIVAL,
                            published_at="2026-06-30",
                            metadata={"price": "¥38,280"},
                        ),
                        RadarItem(
                            source="generic_page",
                            title="Proxy public page",
                            url="https://example.com/proxy",
                            status=ItemStatus.SHOP_NEWS,
                            published_at="2026-06-30",
                            metadata={
                                "shop": {"name": "Proxy Shop"},
                                "item": {"title": "Shell Garden JSK", "url": "https://example.com/proxy/shell"},
                                "matched_keywords": ["JSK"],
                            },
                        ),
                    ],
                )
                record_source_run(
                    connection,
                    "angelic_pretty",
                    ok=False,
                    status="failed",
                    error_rate=1.0,
                    latency_ms=1200,
                    item_count=0,
                    error_message="timeout",
                )
                connection.commit()
            finally:
                connection.close()

            state = get_feed_state(config_path=config_path, db_path=db_path, market_path=market_path)
            feed = state["feed"]

            self.assertEqual(set(feed["streams"]), {"release", "drop", "trend", "alert"})
            self.assertEqual(feed["streams"]["release"][0]["price"], "¥38,280")
            self.assertEqual(feed["streams"]["drop"][0]["shop"], "Proxy Shop")
            self.assertEqual(feed["streams"]["drop"][0]["item"], "Shell Garden JSK")
            self.assertEqual(feed["summary"]["releases"], 1)
            self.assertEqual(feed["summary"]["drops"], 1)
            self.assertEqual(feed["summary"]["shops"], 1)
            self.assertEqual(feed["streams"]["trend"][0]["trend"], "rising")
            self.assertEqual(feed["streams"]["trend"][0]["price_delta"], 0.5)
            self.assertEqual(feed["streams"]["alert"][0]["brand"], "Angelic Pretty")
            self.assertEqual(feed["streams"]["alert"][0]["title"], "Angelic Pretty source unavailable")
            self.assertEqual(feed["streams"]["alert"][0]["meta"], "timeout")
            self.assertEqual(feed["streams"]["alert"][0]["error_rate"], 1.0)
            self.assertEqual(feed["streams"]["alert"][0]["latency_ms"], 1200)
            self.assertEqual(feed["streams"]["alert"][0]["item_count"], 0)
            feed_types = [row["feed_type"] for row in feed["all"]]
            priorities = [{"release": 0, "drop": 1, "alert": 2, "trend": 3}[feed_type] for feed_type in feed_types]
            self.assertEqual(priorities, sorted(priorities))
            self.assertIn("trend", feed_types)

    def test_feed_payload_is_feed_os_contract_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  angelic_pretty:
    type: angelic_pretty
    enabled: true
    url: "https://example.com/ap"
""".strip(),
                encoding="utf-8",
            )
            connection = connect(db_path)
            try:
                diff_and_store(
                    connection,
                    [
                        RadarItem(
                            source="angelic_pretty",
                            title="Shell Garden JSK",
                            url="https://example.com/ap/shell",
                            status=ItemStatus.NEW_ARRIVAL,
                            published_at="2026-06-30",
                            metadata={"price": "¥38,280"},
                        )
                    ],
                )
            finally:
                connection.close()

            payload = get_feed_payload(config_path=config_path, db_path=db_path)

            self.assertTrue(payload["ok"])
            self.assertEqual(set(payload["feed"]["streams"]), {"release", "drop", "trend", "alert"})
            self.assertEqual(payload["feed"]["streams"]["release"][0]["title"], "Shell Garden JSK")
            self.assertEqual(payload["counts"]["items"], 1)
            self.assertNotIn("items", payload)
            self.assertNotIn("events", payload)
            self.assertNotIn("market", payload)

    def test_feed_api_serves_feed_os_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  angelic_pretty:
    type: angelic_pretty
    enabled: true
    url: "https://example.com/ap"
""".strip(),
                encoding="utf-8",
            )
            connection = connect(db_path)
            try:
                diff_and_store(
                    connection,
                    [
                        RadarItem(
                            source="angelic_pretty",
                            title="Shell Garden JSK",
                            url="https://example.com/ap/shell",
                            status=ItemStatus.NEW_ARRIVAL,
                            published_at="2026-06-30",
                        )
                    ],
                )
            finally:
                connection.close()

            handler = make_handler(config_path=config_path, db_path=db_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/feed"
                with urllib.request.urlopen(url) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertTrue(payload["ok"])
                self.assertEqual(response.headers.get_content_type(), "application/json")
                self.assertEqual(set(payload["feed"]["streams"]), {"release", "drop", "trend", "alert"})
                self.assertEqual(payload["feed"]["streams"]["release"][0]["url"], "https://example.com/ap/shell")
                self.assertNotIn("items", payload)
                self.assertNotIn("events", payload)
            finally:
                server.shutdown()
                server.server_close()

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

    def test_check_all_keeps_feed_state_when_one_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  good:
    type: fake_good
    enabled: true
    url: "https://example.com/good"
  bad:
    type: fake_bad
    enabled: true
    url: "https://example.com/bad"
""".strip(),
                encoding="utf-8",
            )

            original = dict(runner.ADAPTERS)
            runner.ADAPTERS.update({"fake_good": FakeGoodAdapter, "fake_bad": FakeBadAdapter})
            handler = make_handler(config_path=config_path, db_path=db_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/check"
                request = urllib.request.Request(
                    url,
                    data=json.dumps({"notify": False}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertTrue(payload["ok"])
                self.assertEqual(payload["checked_source"], "all")
                self.assertEqual(payload["new_event_count"], 1)
                self.assertEqual(payload["new_events"][0]["source"], "good")
                self.assertEqual(payload["new_events"][0]["metadata"]["brand"], "Test Brand")
                self.assertEqual(payload["new_events"][0]["metadata"]["price"], "¥12,000")
                source_alerts = [
                    alert
                    for alert in payload["feed"]["streams"]["alert"]
                    if alert.get("reason_codes") == ["source_health"]
                ]
                self.assertEqual(len(source_alerts), 1)
                self.assertEqual(source_alerts[0]["brand"], "bad")
                self.assertEqual(source_alerts[0]["url"], "https://example.com/bad")
            finally:
                server.shutdown()
                server.server_close()
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

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
